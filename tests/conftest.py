# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for charm tests."""

from typing import Generator

import pytest

_ABORTED_MODULES: dict[object, bool] = {}


def pytest_addoption(parser: pytest.Parser):
    """Parse repository-specific pytest options.

    Args:
        parser: pytest command line parser.
    """
    # The prebuilt charm file.
    parser.addoption("--charm-file", action="append", default=[])


def pytest_configure(config: pytest.Config):
    """Register repository-specific pytest markers.

    Args:
        config: pytest configuration object.
    """
    config.addinivalue_line(
        "markers",
        "abort_on_fail: abort subsequent tests in this module after failure",
    )


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]) -> Generator[None, None, None]:
    """Record abort_on_fail failures for later tests in the same module.

    Args:
        item: pytest test item.
        call: pytest call information.
    """
    outcome = yield
    assert outcome is not None
    report = outcome.get_result()
    setattr(item, "rep_" + report.when, report)
    failed = bool(getattr(item, "failed", False) or report.failed)
    xfailed = bool(getattr(item, "xfailed", False) or getattr(report, "wasxfail", False))
    setattr(item, "failed", failed)
    setattr(item, "xfailed", xfailed)

    abort_on_fail = item.get_closest_marker("abort_on_fail")
    if abort_on_fail and abort_on_fail.kwargs.get("abort_on_xfail", False):
        failed = failed or xfailed
    module = getattr(item, "module", None)
    if failed and abort_on_fail and module is not None:
        _ABORTED_MODULES[module] = True


def pytest_runtest_setup(item: pytest.Item):
    """Xfail remaining tests in a module after an abort_on_fail failure.

    Args:
        item: pytest test item.
    """
    module = getattr(item, "module", None)
    if module is not None and _ABORTED_MODULES.get(module):
        pytest.xfail("aborted")
