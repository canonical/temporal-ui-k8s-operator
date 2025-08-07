# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for jubilant tests."""

import pathlib

import jubilant
import pytest
import yaml


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    keep_models = bool(request.config.getoption("--keep-models"))

    with jubilant.temp_model(keep=keep_models) as model:
        model.wait_timeout = 10 * 60

        yield model

        if request.session.testsfailed:
            log = model.debuglog(limit=1000)
            print(log, end="")


@pytest.fixture(scope="module")
def ui_latest(juju: jubilant.Juju):
    """Deploy temporal-ui from the latest track."""
    juju.model_config(
        values={
            "update-status-hook-interval": "10s",
        },
    )

    juju.deploy(
        charm="postgresql-k8s",
        app="postgresql-k8s",
        channel="14/stable",
        trust=True,
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm="temporal-k8s",
        app="temporal-k8s",
        channel="1.23/edge",
        config={
            "num-history-shards": 1,
        },
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm="temporal-admin-k8s",
        app="temporal-admin-k8s",
        channel="1.23/edge",
        base="ubuntu@22.04",
    )

    juju.deploy(
        charm="temporal-ui-k8s",
        app="temporal-ui-k8s",
        channel="latest/stable",
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

    juju.wait(jubilant.all_active)

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
    metadata = yaml.safe_load(pathlib.Path("./metadata.yaml").read_text())
    return {
        "temporal-ui-image": metadata["containers"]["temporal-ui"]["upstream-source"],
    }
