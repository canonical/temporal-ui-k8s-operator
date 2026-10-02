# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Temporal UI charm integration tests."""

import json
import logging
import pathlib

import jubilant
import pytest
import requests
import yaml
from conftest import POSTGRESQL_K8S_CHANNEL, TEMPORAL_CHANNEL
from helpers import fast_forward

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(pathlib.Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

TEMPORAL_SERVER = "temporal-k8s"
TEMPORAL_ADMIN = "temporal-admin-k8s"
POSTGRESQL_K8S = "postgresql-k8s"
POSTGRESQL_K8S_TRUST = True
TRAEFIK_K8S = "traefik-k8s"
TRAEFIK_K8S_CHANNEL = "latest/stable"
TRAEFIK_K8S_TRUST = True


@pytest.fixture(name="deploy", scope="module")
def deploy(juju: jubilant.Juju, charm_path: pathlib.Path, charm_resources: dict[str, str]):
    """The app is up and running."""
    juju.deploy(TEMPORAL_SERVER, channel=TEMPORAL_CHANNEL, config={"num-history-shards": 1})
    juju.deploy(TEMPORAL_ADMIN, channel=TEMPORAL_CHANNEL)
    juju.deploy(POSTGRESQL_K8S, channel=POSTGRESQL_K8S_CHANNEL, trust=POSTGRESQL_K8S_TRUST)
    juju.deploy(TRAEFIK_K8S, channel=TRAEFIK_K8S_CHANNEL, trust=TRAEFIK_K8S_TRUST)
    juju.deploy(charm_path, app=APP_NAME, resources=charm_resources)

    with fast_forward(juju):
        juju.integrate(f"{TEMPORAL_SERVER}:db", f"{POSTGRESQL_K8S}:database")
        juju.integrate(f"{TEMPORAL_SERVER}:visibility", f"{POSTGRESQL_K8S}:database")
        juju.integrate(f"{TEMPORAL_SERVER}:admin", f"{TEMPORAL_ADMIN}:admin")
        juju.integrate(f"{APP_NAME}:ui", f"{TEMPORAL_SERVER}:ui")
        juju.integrate(f"{APP_NAME}:temporal-host-info", f"{TEMPORAL_SERVER}:temporal-host-info")
        juju.integrate(f"{APP_NAME}:ingress", f"{TRAEFIK_K8S}:ingress")

        juju.wait(
            jubilant.all_active,
            timeout=90 * 10,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for Temporal UI charm as a requirer of ingress."""

    def test_ingress(self, juju: jubilant.Juju):
        """Test connectivity through ingress."""
        task = juju.run(f"{TRAEFIK_K8S}/0", "show-proxied-endpoints")
        endpoint = json.loads(task.results.get("proxied-endpoints")).get("temporal-ui-k8s").get("url")

        assert requests.get(f"{endpoint}/-/ready", timeout=10).status_code == 200
