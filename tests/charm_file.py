# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for locating locally built charm packages during tests."""

import os
import pathlib


def find_built_charm(project_root: pathlib.Path) -> pathlib.Path:
    """Return the built charm package for the project.

    Charmcraft can place built artifacts either in the project root or in a build
    directory, depending on the environment. Search the known output locations and
    require a single match so tests refresh the intended charm.
    """
    search_dirs = []
    if charm_build_dir := os.environ.get("CHARM_BUILD_DIR"):
        search_dirs.append(pathlib.Path(charm_build_dir))

    search_dirs.extend((project_root, project_root / "build"))

    charms = {
        charm.resolve()
        for search_dir in search_dirs
        if search_dir.exists()
        for charm in search_dir.glob("*.charm")
    }

    assert charms, "*.charm not found in project root or build directory"
    assert len(charms) == 1, "More than one *.charm file found, unsure which to use"
    return next(iter(charms))
