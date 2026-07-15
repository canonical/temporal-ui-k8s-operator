# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for charm artifact discovery helpers."""

import pathlib

import pytest

from tests.charm_file import find_built_charm


def test_find_built_charm_prefers_build_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Find the built charm in the default build directory.

    Args:
        tmp_path: Temporary directory fixture for arranging test files.
        monkeypatch: Fixture to isolate environment variable updates.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    build_dir = project_root / "build"
    build_dir.mkdir()
    charm = build_dir / "temporal-ui-k8s_ubuntu-24.04-amd64.charm"
    charm.touch()

    monkeypatch.delenv("CHARM_BUILD_DIR", raising=False)

    assert find_built_charm(project_root) == charm.resolve()


def test_find_built_charm_honours_charm_build_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Prefer an explicit CHARM_BUILD_DIR over default locations.

    Args:
        tmp_path: Temporary directory fixture for arranging test files.
        monkeypatch: Fixture to isolate environment variable updates.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    charm_build_dir = tmp_path / "artifacts"
    charm_build_dir.mkdir()
    charm = charm_build_dir / "temporal-ui-k8s_ubuntu-24.04-amd64.charm"
    charm.touch()

    monkeypatch.setenv("CHARM_BUILD_DIR", str(charm_build_dir))

    assert find_built_charm(project_root) == charm.resolve()
