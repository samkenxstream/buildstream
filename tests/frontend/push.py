import os
import shutil
import pytest
from collections import namedtuple
from unittest.mock import MagicMock

from buildstream._exceptions import ErrorDomain
from tests.testutils import cli, create_artifact_share, create_element_size
from tests.testutils.site import IS_LINUX

# Project directory
DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project",
)


# Assert that a given artifact is in the share
#
def assert_shared(cli, share, project, element_name):
    # NOTE: 'test' here is the name of the project
    # specified in the project.conf we are testing with.
    #
    cache_key = cli.get_element_key(project, element_name)
    if not share.has_artifact('test', element_name, cache_key):
        raise AssertionError("Artifact share at {} does not contain the expected element {}"
                             .format(share.repo, element_name))


# Assert that a given artifact is NOT in the share
#
def assert_not_shared(cli, share, project, element_name):
    # NOTE: 'test' here is the name of the project
    # specified in the project.conf we are testing with.
    #
    cache_key = cli.get_element_key(project, element_name)
    if share.has_artifact('test', element_name, cache_key):
        raise AssertionError("Artifact share at {} unexpectedly contains the element {}"
                             .format(share.repo, element_name))


# Tests that:
#
#  * `bst push` fails if there are no remotes configured for pushing
#  * `bst push` successfully pushes to any remote that is configured for pushing
#
@pytest.mark.datafiles(DATA_DIR)
def test_push(cli, tmpdir, datafiles):
    project = str(datafiles)

    # First build the project without the artifact cache configured
    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_success()

    # Assert that we are now cached locally
    assert cli.get_element_state(project, 'target.bst') == 'cached'

    # Set up two artifact shares.
    share1 = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare1'))
    share2 = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare2'))

    # Try pushing with no remotes configured. This should fail.
    result = cli.run(project=project, args=['push', 'target.bst'])
    result.assert_main_error(ErrorDomain.STREAM, None)

    # Configure bst to pull but not push from a cache and run `bst push`.
    # This should also fail.
    cli.configure({
        'artifacts': {'url': share1.repo, 'push': False},
    })
    result = cli.run(project=project, args=['push', 'target.bst'])
    result.assert_main_error(ErrorDomain.STREAM, None)

    # Configure bst to push to one of the caches and run `bst push`. This works.
    cli.configure({
        'artifacts': [
            {'url': share1.repo, 'push': False},
            {'url': share2.repo, 'push': True},
        ]
    })
    result = cli.run(project=project, args=['push', 'target.bst'])

    assert_not_shared(cli, share1, project, 'target.bst')
    assert_shared(cli, share2, project, 'target.bst')

    # Now try pushing to both (making sure to empty the cache we just pushed
    # to).
    shutil.rmtree(share2.directory)
    share2 = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare2'))
    cli.configure({
        'artifacts': [
            {'url': share1.repo, 'push': True},
            {'url': share2.repo, 'push': True},
        ]
    })
    result = cli.run(project=project, args=['push', 'target.bst'])

    assert_shared(cli, share1, project, 'target.bst')
    assert_shared(cli, share2, project, 'target.bst')


# Tests that `bst push --deps all` pushes all dependencies of the given element.
#
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_push_all(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    share = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'))

    # First build it without the artifact cache configured
    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_success()

    # Assert that we are now cached locally
    assert cli.get_element_state(project, 'target.bst') == 'cached'

    # Configure artifact share
    cli.configure({
        #
        # FIXME: This test hangs "sometimes" if we allow
        #        concurrent push.
        #
        #        It's not too bad to ignore since we're
        #        using the local artifact cache functionality
        #        only, but it should probably be fixed.
        #
        'scheduler': {
            'pushers': 1
        },
        'artifacts': {
            'url': share.repo,
            'push': True,
        }
    })

    # Now try bst push all the deps
    result = cli.run(project=project, args=[
        'push', 'target.bst',
        '--deps', 'all'
    ])
    result.assert_success()

    # And finally assert that all the artifacts are in the share
    assert_shared(cli, share, project, 'target.bst')
    assert_shared(cli, share, project, 'import-bin.bst')
    assert_shared(cli, share, project, 'import-dev.bst')
    assert_shared(cli, share, project, 'compose-all.bst')


# Tests that `bst build` won't push artifacts to the cache it just pulled from.
#
# Regression test for https://gitlab.com/BuildStream/buildstream/issues/233.
@pytest.mark.skipif(not IS_LINUX, reason='Only available on linux')
@pytest.mark.datafiles(DATA_DIR)
def test_push_after_pull(cli, tmpdir, datafiles):
    project = os.path.join(datafiles.dirname, datafiles.basename)

    # Set up two artifact shares.
    share1 = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare1'))
    share2 = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare2'))

    # Set the scene: share1 has the artifact, share2 does not.
    #
    cli.configure({
        'artifacts': {'url': share1.repo, 'push': True},
    })

    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_success()

    share1.update_summary()
    cli.remove_artifact_from_cache(project, 'target.bst')

    assert_shared(cli, share1, project, 'target.bst')
    assert_not_shared(cli, share2, project, 'target.bst')
    assert cli.get_element_state(project, 'target.bst') != 'cached'

    # Now run the build again. Correct `bst build` behaviour is to download the
    # artifact from share1 but not push it back again.
    #
    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_success()
    assert result.get_pulled_elements() == ['target.bst']
    assert result.get_pushed_elements() == []

    # Delete the artifact locally again.
    cli.remove_artifact_from_cache(project, 'target.bst')

    # Now we add share2 into the mix as a second push remote. This time,
    # `bst build` should push to share2 after pulling from share1.
    cli.configure({
        'artifacts': [
            {'url': share1.repo, 'push': True},
            {'url': share2.repo, 'push': True},
        ]
    })
    result = cli.run(project=project, args=['build', 'target.bst'])
    result.assert_success()
    assert result.get_pulled_elements() == ['target.bst']
    assert result.get_pushed_elements() == ['target.bst']


# Ensure that when an artifact's size exceeds available disk space
# the least recently pushed artifact is deleted in order to make room for
# the incoming artifact.
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_expires(cli, datafiles, tmpdir):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = os.path.join(project, 'elements')

    # Create an artifact share (remote artifact cache) in the tmpdir/artifactshare
    share = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'))

    # Mock the os.statvfs() call to return a named tuple which emulates an
    # os.statvfs_result object
    statvfs_result = namedtuple('statvfs_result', 'f_blocks f_bfree f_bsize')
    os.statvfs = MagicMock(return_value=statvfs_result(f_blocks=int(10e9),
                                                       f_bfree=(int(12e6) + int(2e9)),
                                                       f_bsize=1))

    # Configure bst to push to the cache
    cli.configure({
        'artifacts': {'url': share.repo, 'push': True},
    })

    # Create and build an element of 5 MB
    create_element_size('element1.bst', element_path, [], int(5e6))  # [] => no deps
    result = cli.run(project=project, args=['build', 'element1.bst'])
    result.assert_success()

    # Create and build an element of 5 MB
    create_element_size('element2.bst', element_path, [], int(5e6))  # [] => no deps
    result = cli.run(project=project, args=['build', 'element2.bst'])
    result.assert_success()

    # update the share
    share.update_summary()

    # check that element's 1 and 2 are cached both locally and remotely
    assert cli.get_element_state(project, 'element1.bst') == 'cached'
    assert_shared(cli, share, project, 'element1.bst')
    assert cli.get_element_state(project, 'element2.bst') == 'cached'
    assert_shared(cli, share, project, 'element2.bst')

    # update mocked available disk space now that two 5 MB artifacts have been added
    os.statvfs = MagicMock(return_value=statvfs_result(f_blocks=int(10e9),
                                                       f_bfree=(int(2e6) + int(2e9)),
                                                       f_bsize=1))

    # Create and build another element of 5 MB (This will exceed the free disk space available)
    create_element_size('element3.bst', element_path, [], int(5e6))
    result = cli.run(project=project, args=['build', 'element3.bst'])
    result.assert_success()

    # update the share
    share.update_summary()

    # Ensure it is cached both locally and remotely
    assert cli.get_element_state(project, 'element3.bst') == 'cached'
    assert_shared(cli, share, project, 'element3.bst')

    # Ensure element1 has been removed from the share
    assert_not_shared(cli, share, project, 'element1.bst')
    # Ensure that elemen2 remains
    assert_shared(cli, share, project, 'element2.bst')


# Test that a large artifact, whose size exceeds the quota, is not pushed
# to the remote share
@pytest.mark.datafiles(DATA_DIR)
def test_artifact_too_large(cli, datafiles, tmpdir):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = os.path.join(project, 'elements')

    # Create an artifact share (remote cache) in tmpdir/artifactshare
    share = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'))

    # Mock a file system with 5 MB total space
    statvfs_result = namedtuple('statvfs_result', 'f_blocks f_bfree f_bsize')
    os.statvfs = MagicMock(return_value=statvfs_result(f_blocks=int(5e6) + int(2e9),
                                                       f_bfree=(int(5e6) + int(2e9)),
                                                       f_bsize=1))

    # Configure bst to push to the remote cache
    cli.configure({
        'artifacts': {'url': share.repo, 'push': True},
    })

    # Create and push a 3MB element
    create_element_size('small_element.bst', element_path, [], int(3e6))
    result = cli.run(project=project, args=['build', 'small_element.bst'])
    result.assert_success()

    # Create and try to push a 6MB element.
    create_element_size('large_element.bst', element_path, [], int(6e6))
    result = cli.run(project=project, args=['build', 'large_element.bst'])
    result.assert_success()

    # update the cache
    share.update_summary()

    # Ensure that the small artifact is still in the share
    assert cli.get_element_state(project, 'small_element.bst') == 'cached'
    assert_shared(cli, share, project, 'small_element.bst')

    # Ensure that the artifact is cached locally but NOT remotely
    assert cli.get_element_state(project, 'large_element.bst') == 'cached'
    assert_not_shared(cli, share, project, 'large_element.bst')


# Test that when an element is pulled recently, it is not considered the LRU element.
# NOTE: We expect this test to fail as the current implementation of remote cache
# expiry only expiries from least recently pushed. NOT least recently used. This will
# hopefully change when we implement as CAS cache.
@pytest.mark.xfail
@pytest.mark.datafiles(DATA_DIR)
def test_recently_pulled_artifact_does_not_expire(cli, datafiles, tmpdir):
    project = os.path.join(datafiles.dirname, datafiles.basename)
    element_path = os.path.join(project, 'elements')

    # Create an artifact share (remote cache) in tmpdir/artifactshare
    share = create_artifact_share(os.path.join(str(tmpdir), 'artifactshare'))

    # Mock a file system with 12 MB free disk space
    statvfs_result = namedtuple('statvfs_result', 'f_blocks f_bfree f_bsize')
    os.statvfs = MagicMock(return_value=statvfs_result(f_blocks=int(10e9) + int(2e9),
                                                       f_bfree=(int(12e6) + int(2e9)),
                                                       f_bsize=1))

    # Configure bst to push to the cache
    cli.configure({
        'artifacts': {'url': share.repo, 'push': True},
    })

    # Create and build 2 elements, each of 5 MB.
    create_element_size('element1.bst', element_path, [], int(5e6))
    result = cli.run(project=project, args=['build', 'element1.bst'])
    result.assert_success()

    create_element_size('element2.bst', element_path, [], int(5e6))
    result = cli.run(project=project, args=['build', 'element2.bst'])
    result.assert_success()

    share.update_summary()

    # Ensure they are cached locally
    assert cli.get_element_state(project, 'element1.bst') == 'cached'
    assert cli.get_element_state(project, 'element2.bst') == 'cached'

    # Ensure that they have  been pushed to the cache
    assert_shared(cli, share, project, 'element1.bst')
    assert_shared(cli, share, project, 'element2.bst')

    # Remove element1 from the local cache
    cli.remove_artifact_from_cache(project, 'element1.bst')
    assert cli.get_element_state(project, 'element1.bst') != 'cached'

    # Pull the element1 from the remote cache (this should update its mtime)
    result = cli.run(project=project, args=['pull', 'element1.bst', '--remote',
                                            share.repo])
    result.assert_success()

    # Ensure element1 is cached locally
    assert cli.get_element_state(project, 'element1.bst') == 'cached'

    # Create and build the element3 (of 5 MB)
    create_element_size('element3.bst', element_path, [], int(5e6))
    result = cli.run(project=project, args=['build', 'element3.bst'])
    result.assert_success()

    share.update_summary()

    # Make sure it's cached locally and remotely
    assert cli.get_element_state(project, 'element3.bst') == 'cached'
    assert_shared(cli, share, project, 'element3.bst')

    # Ensure that element2 was deleted from the share and element1 remains
    assert_not_shared(cli, share, project, 'element2.bst')
    assert_shared(cli, share, project, 'element1.bst')
