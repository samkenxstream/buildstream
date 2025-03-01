#
#  Copyright (C) 2019 Codethink Limited
#  Copyright (C) 2019 Bloomberg Finance LP
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""
This package contains various utilities which make it easier to test plugins.
"""

import os
from collections import OrderedDict
from typing import Tuple
from buildstream.exceptions import ErrorDomain, LoadErrorReason
from ._yaml import generate_project, generate_element, load_yaml
from .repo import Repo
from .runcli import cli, cli_integration, cli_remote_execution, Cli
from .integration import integration_cache
from ._cachekeys import check_cache_key_stability

__all__ = [
    "check_cache_key_stability",
    "create_repo",
    "register_repo_kind",
    "sourcetests_collection_hook",
]

# To make use of these test utilities it is necessary to have pytest
# available. However, we don't want to have a hard dependency on
# pytest.
try:
    import pytest
except ImportError:
    module_name = globals()["__name__"]
    msg = "Could not import pytest:\n" "To use the {} module, you must have pytest installed.".format(module_name)
    raise ImportError(msg)


# Of the form plugin_name -> (repo_class, plugin_package)
ALL_REPO_KINDS = OrderedDict()  # type: OrderedDict[str, Tuple[Repo, str]]


def create_repo(kind, directory, subdir="repo"):
    """Convenience method for creating a Repo

    Args:
        kind (str): The kind of repo to create (a source plugin basename). This
                    must have previously been registered using
                    `register_repo_kind`
        directory (str): The path where the repo will keep a cache

    Returns:
        (Repo): A new Repo object
    """
    try:
        constructor = ALL_REPO_KINDS[kind]
    except KeyError as e:
        raise AssertionError("Unsupported repo kind {}".format(kind)) from e

    return constructor[0](directory, subdir=subdir)


def register_repo_kind(kind, cls, plugin_package):
    """Register a new repo kind.

    Registering a repo kind will allow the use of the `create_repo`
    method for that kind and include that repo kind in ALL_REPO_KINDS

    In addition, repo_kinds registred prior to
    `sourcetests_collection_hook` being called will be automatically
    used to test the basic behaviour of their associated source
    plugins using the tests in `testing._sourcetests`.

    Args:
       kind (str): The kind of repo to create (a source plugin basename)
       cls (cls) : A class derived from Repo.
       plugin_package (str): The name of the python package containing the plugin

    """
    ALL_REPO_KINDS[kind] = (cls, plugin_package)


def sourcetests_collection_hook(session):
    """Used to hook the templated source plugin tests into a pyest test suite.

    This should be called via the `pytest_sessionstart
    hook <https://docs.pytest.org/en/latest/reference.html#collection-hooks>`_.
    The tests in the _sourcetests package will be collected as part of
    whichever test package this hook is called from.

    Args:
        session (pytest.Session): The current pytest session
    """

    def should_collect_tests(config):
        args = config.args
        rootdir = config.rootdir
        # When no args are supplied, pytest defaults the arg list to
        # just include the session's root_dir. We want to collect
        # tests as part of the default collection
        if args == [str(rootdir)]:
            return True

        # If specific tests are passed, don't collect
        # everything. Pytest will handle this correctly without
        # modification.
        if len(args) > 1 or rootdir not in args:
            return False

        # If in doubt, collect them, this will be an easier bug to
        # spot and is less likely to result in bug not being found.
        return True

    from . import _sourcetests

    source_test_path = os.path.dirname(_sourcetests.__file__)
    # Add the location of the source tests to the session's
    # python_files config. Without this, pytest may filter out these
    # tests during collection.
    session.config.addinivalue_line("python_files", os.path.join(source_test_path, "*.py"))
    # If test invocation has specified specic tests, don't
    # automatically collect templated tests.
    if should_collect_tests(session.config):
        session.config.args.append(source_test_path)
