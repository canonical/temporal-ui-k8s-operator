# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for jubilant tests."""

import pathlib

import jubilant
import pytest
import yaml

POSTGRESQL_K8S_CHANNEL = "14/stable"
TEMPORAL_CHANNEL = "1.23/edge"

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
):
    """Helper function to deploy the temporal stack.

    Args:
        juju: jubilant Juju object
        postgresql_channel: channel for postgresql-k8s
        temporal_server_channel: channel for temporal-k8s
        temporal_admin_channel: channel for temporal-admin-k8s
        temporal_ui_channel: channel for temporal-ui-k8s
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
        charm="temporal-k8s",
        app="temporal-k8s",
        channel=temporal_server_channel,
        config={
            "num-history-shards": 1,
        },
        base="ubuntu@22.04",
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
            and jubilant.all_blocked(status, "temporal-k8s", "temporal-admin-k8s", "temporal-ui-k8s")
        ),
    )

    juju.integrate("temporal-k8s:db", "postgresql-k8s:database")
    juju.integrate("temporal-k8s:visibility", "postgresql-k8s:database")

    juju.integrate("temporal-k8s:admin", "temporal-admin-k8s:admin")

    juju.integrate("temporal-k8s:ui", "temporal-ui-k8s:ui")
    try:
        juju.integrate("temporal-k8s:temporal-host-info", "temporal-ui-k8s:temporal-host-info")
    except jubilant.CLIError as exc:
        msg = str(exc).lower()
        if "temporal-host-info" in msg and "has no" in msg:
            juju.config("temporal-ui-k8s", {"server-name": "temporal-k8s"})
        else:
            raise

    juju.wait(jubilant.all_active)


@pytest.fixture(scope="module")
def ui_latest_track(juju: jubilant.Juju):
    """Deploy the temporal stack with temporal-ui from the latest/edge track."""
    deploy_temporal_stack(juju, temporal_ui_channel="latest/edge")

    return "temporal-ui-k8s"


@pytest.fixture(scope="module")
def charm_path() -> pathlib.Path:
    """Returns the absolute path of the locally built ui-k8s charm."""
    charm_dir = pathlib.Path(__file__).parent.parent.parent
    charms = [p.absolute() for p in charm_dir.glob("*.charm")]
    assert charms, "*.charm not found in project root"
    assert len(charms) == 1, "More than one *.charm file found in project root, unsure which to use"
    return charms[0]


@pytest.fixture(scope="module")
def charm_resources() -> dict:
    """Resources to deploy the ui-k8s locally built charm."""
    return {
        "temporal-ui-image": TEMPORAL_UI_IMAGE,
    }
