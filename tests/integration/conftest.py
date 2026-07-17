# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for jubilant tests."""

import pathlib

import jubilant
import pytest
import pytest_asyncio
import yaml
from pytest_operator.plugin import OpsTest

POSTGRESQL_K8S_CHANNEL = "14/stable"

# Temporal charm channels. Bump these when the charms migrate to a new track
# (e.g. 1.23 -> 1.31).
TEMPORAL_CHANNEL = "1.31/edge"  # server/admin dependencies and the default ui deploy
TEMPORAL_UI_LATEST_RELEASE_CHANNEL = "1.23/stable"  # published ui release the refresh test upgrades from

TEMPORAL_SERVER_JUJU_APP = "temporal-k8s"

METADATA = yaml.safe_load(pathlib.Path("./metadata.yaml").read_text())
TEMPORAL_UI_IMAGE = METADATA["resources"]["temporal-ui-image"]["upstream-source"]


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    keep_models = bool(request.config.getoption("--keep-models"))

    with jubilant.temp_model(keep=keep_models) as model:
        model.wait_timeout = 10 * 60

        yield model

        if request.session.testsfailed:
            log = model.debug_log(limit=1000)
            print(log, end="")


def deploy_temporal_stack(
    juju: jubilant.Juju,
    postgresql_channel: str = POSTGRESQL_K8S_CHANNEL,
    temporal_server_channel: str = TEMPORAL_CHANNEL,
    temporal_admin_channel: str = TEMPORAL_CHANNEL,
    temporal_ui_channel: str = TEMPORAL_CHANNEL,
    integrate_host_info: bool = True,
):
    """Helper function to deploy the temporal stack.

    Args:
        juju: jubilant Juju object
        postgresql_channel: channel for postgresql-k8s
        temporal_server_channel: channel for temporal-k8s
        temporal_admin_channel: channel for temporal-admin-k8s
        temporal_ui_channel: channel for temporal-ui-k8s
        integrate_host_info: whether to integrate the temporal-host-info relation
            during deployment (default True).
    """
    juju.model_config(
        values={
            "update-status-hook-interval": "10s",
        },
    )

    juju.deploy(
        charm="postgresql-k8s",
        app="postgresql-k8s",
        channel=postgresql_channel,
        trust=True,
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm=TEMPORAL_SERVER_JUJU_APP,
        app=TEMPORAL_SERVER_JUJU_APP,
        channel=temporal_server_channel,
        config={
            "num-history-shards": 1,
        },
        base="ubuntu@24.04",
    )

    juju.deploy(
        charm="temporal-admin-k8s",
        app="temporal-admin-k8s",
        channel=temporal_admin_channel,
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm="temporal-ui-k8s",
        app="temporal-ui-k8s",
        channel=temporal_ui_channel,
        base="ubuntu@22.04",
    )

    juju.wait(
        lambda status: (
            jubilant.all_active(status, "postgresql-k8s")
            and jubilant.all_blocked(status, TEMPORAL_SERVER_JUJU_APP, "temporal-admin-k8s", "temporal-ui-k8s")
        ),
    )

    juju.integrate(f"{TEMPORAL_SERVER_JUJU_APP}:db", "postgresql-k8s:database")
    juju.integrate(f"{TEMPORAL_SERVER_JUJU_APP}:visibility", "postgresql-k8s:database")

    juju.integrate(f"{TEMPORAL_SERVER_JUJU_APP}:admin", "temporal-admin-k8s:admin")

    juju.integrate(f"{TEMPORAL_SERVER_JUJU_APP}:ui", "temporal-ui-k8s:ui")
    if integrate_host_info:
        juju.integrate(
            f"{TEMPORAL_SERVER_JUJU_APP}:temporal-host-info",
            "temporal-ui-k8s:temporal-host-info",
        )

    juju.wait(jubilant.all_active)


@pytest.fixture(scope="module")
def ui_latest_track(juju: jubilant.Juju):
    """Deploy the temporal stack with temporal-ui from the latest supported release.

    temporal-host-info is integrated during deployment because the latest supported
    release already requires the relation, so the charm would otherwise stay blocked.
    The refresh test then upgrades this deployment to the newer, locally built charm.
    """
    deploy_temporal_stack(juju, temporal_ui_channel=TEMPORAL_UI_LATEST_RELEASE_CHANNEL)

    return "temporal-ui-k8s"


@pytest_asyncio.fixture(scope="module")
async def charm_path(request: pytest.FixtureRequest, ops_test: OpsTest) -> str | pathlib.Path:
    """Build (or locate via --charm-file) the ui-k8s charm and return its path.

    Uses pytest-operator's build_charm so the artifact is managed the same way as
    the rest of the integration suite. Relying on a pre-packed charm in the project
    root or build/ does not work: pytest-operator's build_charm relocates root
    *.charm files and deletes the build/ directory.
    """
    if charms := request.config.getoption("--charm-file"):
        return charms[0]
    charm = await ops_test.build_charm(".")
    assert charm, "Charm not built"
    return charm


@pytest.fixture(scope="module")
def charm_resources() -> dict:
    """Resources to deploy the ui-k8s locally built charm."""
    return {
        "temporal-ui-image": TEMPORAL_UI_IMAGE,
    }
