# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Temporal UI charm integration tests."""

import asyncio
import logging
import socket
import unittest.mock
from pathlib import Path

import pytest
import pytest_asyncio
import requests
import yaml
from helpers import gen_patch_getaddrinfo, scale
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

APP_NAME_SERVER = "temporal-k8s"
APP_NAME_ADMIN = "temporal-admin-k8s"


@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest):
    """The app is up and running."""
    # Deploy temporal server, temporal admin and postgresql charms.
    asyncio.gather(
        ops_test.model.deploy(APP_NAME_SERVER, channel="stable", config={"num-history-shards": 1}),
        ops_test.model.deploy(APP_NAME_ADMIN, channel="stable"),
        ops_test.model.deploy("postgresql-k8s", channel="14", trust=True),
        ops_test.model.deploy("nginx-ingress-integrator", channel="edge", revision=100, trust=True),
    )

    charm = await ops_test.build_charm(".")
    resources = {"temporal-ui-image": METADATA["containers"]["temporal-ui"]["upstream-source"]}

    await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, APP_NAME_SERVER, APP_NAME_ADMIN],
            status="blocked",
            raise_on_blocked=False,
            timeout=600,
        )
        await ops_test.model.wait_for_idle(
            apps=["postgresql-k8s"],
            status="active",
            raise_on_blocked=False,
            timeout=1200,
        )

        await ops_test.model.wait_for_idle(
            apps=["nginx-ingress-integrator"],
            status="waiting",
            raise_on_blocked=False,
            timeout=1200,
        )

        assert ops_test.model.applications[APP_NAME].units[0].workload_status == "blocked"
        await ops_test.model.integrate(f"{APP_NAME_SERVER}:db", "postgresql-k8s:database")
        await ops_test.model.integrate(f"{APP_NAME_SERVER}:visibility", "postgresql-k8s:database")
        await ops_test.model.integrate(f"{APP_NAME_SERVER}:admin", f"{APP_NAME_ADMIN}:admin")

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME_SERVER, APP_NAME_ADMIN],
            status="active",
            raise_on_blocked=False,
            timeout=300,
        )

        await ops_test.model.integrate(f"{APP_NAME}:ui", f"{APP_NAME_SERVER}:ui")

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=300,
        )

        await ops_test.model.integrate(f"{APP_NAME}:nginx-route", "nginx-ingress-integrator:nginx-route")

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, "nginx-ingress-integrator"],
            status="active",
            raise_on_blocked=False,
            timeout=300,
        )

        assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for Temporal UI charm."""

    async def test_basic_client(self, ops_test: OpsTest):
        """Perform GET request on the Temporal UI host."""
        status = await ops_test.model.get_status()  # noqa: F821
        address = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]
        url = f"http://{address}:8080"
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300)
        assert response.status_code == 200

    async def test_ingress(self, ops_test: OpsTest):
        """Set external-hostname and test connectivity through ingress."""
        new_hostname = "temporal-web"
        application = ops_test.model.applications[APP_NAME]
        await application.set_config({"external-hostname": new_hostname})

        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[APP_NAME, "nginx-ingress-integrator"],
                status="active",
                raise_on_blocked=False,
                idle_period=30,
                timeout=1200,
            )

            with unittest.mock.patch.multiple(socket, getaddrinfo=gen_patch_getaddrinfo(new_hostname, "127.0.0.1")):
                response = requests.get(f"https://{new_hostname}", timeout=5, verify=False)  # nosec
                assert response.status_code == 200 and 'id="svelte"' in response.text.lower()

    async def test_restart_action(self, ops_test: OpsTest):
        """Test charm restart action."""
        action = await ops_test.model.applications[APP_NAME].units[0].run_action("restart")
        await action.wait()

        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[APP_NAME],
                status="active",
                raise_on_blocked=False,
                timeout=600,
            )

            assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"

    async def test_scaling_up(self, ops_test: OpsTest):
        """Scale Temporal worker charm up to 2 units."""
        await scale(ops_test, app=APP_NAME, units=2)
