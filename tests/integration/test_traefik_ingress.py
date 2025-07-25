# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Temporal UI charm integration tests."""

import asyncio
import json
import logging
from pathlib import Path

import pytest
import pytest_asyncio
import requests
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

TEMPORAL_SERVER = "temporal-k8s"
TEMPORAL_SERVER_CHANNEL = "stable"
TEMPORAL_ADMIN = "temporal-admin-k8s"
TEMPORAL_ADMIN_CHANNEL = "stable"
POSTGRESQL_K8S = "postgresql-k8s"
POSTGRESQL_K8S_CHANNEL = "14"
POSTGRESQL_K8S_TRUST = True
TRAEFIK_K8S = "traefik-k8s"
TRAEFIK_K8S_CHANNEL = "latest/stable"
TRAEFIK_K8S_TRUST = True


@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest):
    """The app is up and running."""
    # Deploy temporal server, temporal admin, traefik-k8s, and postgresql charms.
    asyncio.gather(
        ops_test.model.deploy(TEMPORAL_SERVER, channel=TEMPORAL_SERVER_CHANNEL, config={"num-history-shards": 1}),
        ops_test.model.deploy(TEMPORAL_ADMIN, channel=TEMPORAL_ADMIN_CHANNEL),
        ops_test.model.deploy(POSTGRESQL_K8S, channel=POSTGRESQL_K8S_CHANNEL, trust=POSTGRESQL_K8S_TRUST),
        ops_test.model.deploy(TRAEFIK_K8S, channel=TRAEFIK_K8S_CHANNEL, trust=TRAEFIK_K8S_TRUST),
    )

    # Build and deploy temporal-ui-k8s
    charm = await ops_test.build_charm(".")
    resources = {"temporal-ui-image": METADATA["containers"]["temporal-ui"]["upstream-source"]}
    await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME)

    # Add all required relations
    async with ops_test.fast_forward():
        await ops_test.model.integrate(f"{TEMPORAL_SERVER}:db", f"{POSTGRESQL_K8S}:database")
        await ops_test.model.integrate(f"{TEMPORAL_SERVER}:visibility", f"{POSTGRESQL_K8S}:database")
        await ops_test.model.integrate(f"{TEMPORAL_SERVER}:admin", f"{TEMPORAL_ADMIN}:admin")
        await ops_test.model.integrate(f"{APP_NAME}:ui", f"{TEMPORAL_SERVER}:ui")
        await ops_test.model.integrate(f"{APP_NAME}:ingress", f"{TRAEFIK_K8S}:ingress")

        await ops_test.model.wait_for_idle(
            status="active",
            raise_on_blocked=False,
            timeout=90 * 10,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for Temporal UI charm as a requirer of ingress."""

    async def test_ingress(self, ops_test: OpsTest):
        """Test connectivity through ingress."""
        traefik_app = ops_test.model.applications.get(TRAEFIK_K8S)
        show_proxified_endpoints = await traefik_app.units[0].run_action("show-proxied-endpoints")
        await show_proxified_endpoints.wait()

        endpoint = (
            json.loads(show_proxified_endpoints.results.get("proxied-endpoints")).get("temporal-ui-k8s").get("url")
        )

        assert requests.get(f"{endpoint}/-/ready", timeout=10).status_code == 200
