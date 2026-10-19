# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import jubilant
import pytest
from conftest import POSTGRESQL_K8S_CHANNEL, TEMPORAL_CHANNEL, TEMPORAL_SERVER_JUJU_APP

UI_APP_NAME = "temporal-ui-k8s"

# The latest track is closed on Charmhub. Deploy the legacy charm by
# revision through an open channel, supplying its original OCI resource
# to avoid resolving resources through the closed channel
TEMPORAL_UI_LEGACY_CHANNEL = "1.23/stable"
TEMPORAL_UI_LEGACY_REVISION = 14
TEMPORAL_UI_LEGACY_RESOURCES = {"temporal-ui-image": "temporalio/ui:2.27.1"}


@pytest.fixture(scope="module")
def ui_latest_track(juju: jubilant.Juju):
    """Deploy the Temporal stack with the legacy temporal-ui revision."""
    juju.model_config(
        values={
            "update-status-hook-interval": "10s",
        },
    )

    juju.deploy(
        charm="postgresql-k8s",
        app="postgresql-k8s",
        channel=POSTGRESQL_K8S_CHANNEL,
        trust=True,
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm=TEMPORAL_SERVER_JUJU_APP,
        app=TEMPORAL_SERVER_JUJU_APP,
        channel=TEMPORAL_CHANNEL,
        config={"num-history-shards": 1},
        base="ubuntu@24.04",
    )

    juju.deploy(
        charm="temporal-admin-k8s",
        app="temporal-admin-k8s",
        channel=TEMPORAL_CHANNEL,
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm=UI_APP_NAME,
        app=UI_APP_NAME,
        channel=TEMPORAL_UI_LEGACY_CHANNEL,
        revision=TEMPORAL_UI_LEGACY_REVISION,
        resources=TEMPORAL_UI_LEGACY_RESOURCES,
        base="ubuntu@22.04",
    )

    juju.wait(
        lambda status: (
            jubilant.all_active(status, "postgresql-k8s")
            and jubilant.all_blocked(
                status,
                TEMPORAL_SERVER_JUJU_APP,
                "temporal-admin-k8s",
                UI_APP_NAME,
            )
        ),
    )

    juju.integrate(
        f"{TEMPORAL_SERVER_JUJU_APP}:db",
        "postgresql-k8s:database",
    )
    juju.integrate(
        f"{TEMPORAL_SERVER_JUJU_APP}:visibility",
        "postgresql-k8s:database",
    )
    juju.integrate(
        f"{TEMPORAL_SERVER_JUJU_APP}:admin",
        "temporal-admin-k8s:admin",
    )
    juju.integrate(
        f"{TEMPORAL_SERVER_JUJU_APP}:ui",
        f"{UI_APP_NAME}:ui",
    )

    juju.wait(jubilant.all_active)

    return UI_APP_NAME


def test_refresh_from_latest_to_1_23(juju: jubilant.Juju, ui_latest_track, charm_path, charm_resources):
    """Test to refresh from latest track to the 1.23 track."""
    juju.refresh(
        ui_latest_track,
        path=charm_path,
        resources=charm_resources,
        base="ubuntu@24.04",
    )

    # Wait for the refreshed charm to settle: it requires temporal-host-info and will
    # be blocked until the relation is established. This ensures upgrade hooks complete
    # before we trigger temporal-k8s to process the new relation-joined hook.
    juju.wait(lambda status: jubilant.all_blocked(status, ui_latest_track))

    juju.integrate(
        f"{TEMPORAL_SERVER_JUJU_APP}:temporal-host-info",
        f"{ui_latest_track}:temporal-host-info",
    )

    juju.wait(jubilant.all_active, error=jubilant.any_error)
