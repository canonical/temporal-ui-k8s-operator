# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test to ensure successful refreshes from latest track to the 1.23 track."""

import logging

import jubilant
from conftest import TEMPORAL_SERVER_JUJU_APP

logger = logging.getLogger(__name__)


def test_refresh_from_latest_to_1_23(juju: jubilant.Juju, ui_latest_track, charm_path, charm_resources):
    """Test to refresh from latest track to the 1.23 track."""
    juju.refresh(
        ui_latest_track,
        path=charm_path,
        resources=charm_resources,
    )

    juju.integrate(
        f"{TEMPORAL_SERVER_JUJU_APP}:temporal-host-info",
        f"{ui_latest_track}:temporal-host-info",
    )

    juju.wait(jubilant.all_active, error=jubilant.any_error)
