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
from conftest import POSTGRESQL_K8S_CHANNEL, TEMPORAL_CHANNEL
from helpers import gen_patch_getaddrinfo, integrate_temporal_host_info_or_set_server_name, scale
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

APP_NAME_SERVER = "temporal-k8s"
APP_NAME_ADMIN = "temporal-admin-k8s"

NGINX_INGRESS_INTEGRATOR_CHANNEL = "latest/edge"

_TEMPORAL_SERVER_SUPPORTS_HOST_INFO: bool | None = None


@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest):
    """The app is up and running."""
    global _TEMPORAL_SERVER_SUPPORTS_HOST_INFO

    # Deploy temporal server, temporal admin and postgresql charms.
    await asyncio.gather(
        ops_test.model.deploy(APP_NAME_SERVER, channel=TEMPORAL_CHANNEL, config={"num-history-shards": 1}),
        ops_test.model.deploy(APP_NAME_ADMIN, channel=TEMPORAL_CHANNEL),
        ops_test.model.deploy("postgresql-k8s", channel=POSTGRESQL_K8S_CHANNEL, trust=True),
        ops_test.model.deploy(
            "nginx-ingress-integrator",
            channel=NGINX_INGRESS_INTEGRATOR_CHANNEL,
            revision=100,
            trust=True,
            config={"ingress-class": "nginx"},
        ),
    )

    charm = await ops_test.build_charm(".")
    resources = {"temporal-ui-image": METADATA["resources"]["temporal-ui-image"]["upstream-source"]}

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
        _TEMPORAL_SERVER_SUPPORTS_HOST_INFO = await integrate_temporal_host_info_or_set_server_name(
            ops_test, ui_app=APP_NAME, temporal_server_app=APP_NAME_SERVER
        )

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
            exit_code, stdout, stderr = await ops_test.run(
                "kubectl",
                "-n",
                "ingress-nginx",
                "get",
                "svc",
                "ingress-nginx-controller",
                "-o",
                "jsonpath={.status.loadBalancer.ingress[0].ip}",
            )
            ingress_ip = stdout.strip()

            with unittest.mock.patch.multiple(socket, getaddrinfo=gen_patch_getaddrinfo(new_hostname, ingress_ip)):
                response = requests.get(
                    f"https://{ingress_ip}",
                    headers={"Host": new_hostname},
                    timeout=10,
                    verify=False,  # nosec
                )
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

    async def test_host_info_relation(self, ops_test: OpsTest):
        """Test that host-info relation takes precedence over deprecated fallback config."""
        if _TEMPORAL_SERVER_SUPPORTS_HOST_INFO is not True:
            pytest.skip("temporal-k8s on this channel has no temporal-host-info relation")
        await ops_test.model.applications[APP_NAME].set_config({"server-name": "deprecated-host"})
        status = await ops_test.model.get_status()  # noqa: F821
        await ops_test.model.integrate(f"{APP_NAME}:temporal-host-info", f"{APP_NAME_SERVER}:temporal-host-info")
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[APP_NAME, APP_NAME_SERVER],
                status="active",
                raise_on_blocked=False,
                timeout=600,
            )

        server_address = status["applications"][APP_NAME_SERVER]["units"][f"{APP_NAME_SERVER}/0"]["address"]

        unit = ops_test.model.applications[APP_NAME].units[0]
        result = await unit.run("cat /home/ui-server/config/charm.yaml")
        charm_config = yaml.safe_load(result.stdout)
        assert charm_config["temporalGrpcAddress"] == f"{server_address}:7233"

    async def test_host_info_relation_removed_falls_back_to_config(self, ops_test: OpsTest):
        """Test that removing host-info relation falls back to deprecated config value."""
        if _TEMPORAL_SERVER_SUPPORTS_HOST_INFO is not True:
            pytest.skip("temporal-k8s on this channel has no temporal-host-info relation")
        await ops_test.model.applications[APP_NAME].set_config({"server-name": "fallback-host"})
        await ops_test.juju(
            "remove-relation",
            f"{APP_NAME}:temporal-host-info",
            f"{APP_NAME_SERVER}:temporal-host-info",
        )
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[APP_NAME, APP_NAME_SERVER],
                status="active",
                raise_on_blocked=False,
                timeout=600,
            )

        unit = ops_test.model.applications[APP_NAME].units[0]
        result = await unit.run("cat /home/ui-server/config/charm.yaml")
        charm_config = yaml.safe_load(result.stdout)
        assert charm_config["temporalGrpcAddress"] == "fallback-host:7233"
