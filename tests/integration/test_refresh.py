# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test to ensure successful refreshes from the latest supported release to the newer charm."""

import logging

import jubilant

logger = logging.getLogger(__name__)


def test_refresh_from_latest_to_1_23(juju: jubilant.Juju, ui_latest_track, charm_path, charm_resources):
    """Refresh from the latest supported temporal-ui-k8s release to the local build."""
    juju.refresh(
        ui_latest_track,
        path=charm_path,
        resources=charm_resources,
        base="ubuntu@24.04",
    )

    # temporal-host-info is already integrated by the ui_latest_track fixture, so the
    # refreshed charm should settle back to active without further intervention.
    juju.wait(jubilant.all_active, error=jubilant.any_error)
