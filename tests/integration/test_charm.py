# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Temporal UI charm integration tests."""

import logging
import pathlib
import socket
import subprocess
import unittest.mock

import jubilant
import pytest
import requests
import yaml
from conftest import POSTGRESQL_K8S_CHANNEL, TEMPORAL_CHANNEL
from helpers import fast_forward, gen_patch_getaddrinfo, scale, unit_status

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(pathlib.Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

APP_NAME_SERVER = "temporal-k8s"
APP_NAME_ADMIN = "temporal-admin-k8s"

NGINX_INGRESS_INTEGRATOR_CHANNEL = "latest/edge"


@pytest.fixture(name="deploy", scope="module")
def deploy(juju: jubilant.Juju, charm_path: pathlib.Path, charm_resources: dict[str, str]):
    """The app is up and running."""
    juju.deploy(APP_NAME_SERVER, channel=TEMPORAL_CHANNEL, config={"num-history-shards": 1})
    juju.deploy(APP_NAME_ADMIN, channel=TEMPORAL_CHANNEL)
    juju.deploy("postgresql-k8s", channel=POSTGRESQL_K8S_CHANNEL, trust=True)
    juju.deploy(
        "nginx-ingress-integrator",
        channel=NGINX_INGRESS_INTEGRATOR_CHANNEL,
        revision=100,
        trust=True,
        config={"ingress-class": "nginx"},
    )
    juju.deploy(charm_path, app=APP_NAME, resources=charm_resources)

    with fast_forward(juju):
        juju.wait(
            lambda status: jubilant.all_blocked(status, APP_NAME, APP_NAME_SERVER, APP_NAME_ADMIN),
            timeout=600,
        )
        juju.wait(
            lambda status: jubilant.all_active(status, "postgresql-k8s"),
            timeout=1200,
        )
        juju.wait(
            lambda status: jubilant.all_waiting(status, "nginx-ingress-integrator"),
            timeout=1200,
        )

        juju.integrate(f"{APP_NAME_SERVER}:db", "postgresql-k8s:database")
        juju.integrate(f"{APP_NAME_SERVER}:visibility", "postgresql-k8s:database")
        juju.integrate(f"{APP_NAME_SERVER}:admin", f"{APP_NAME_ADMIN}:admin")

        juju.wait(
            lambda status: jubilant.all_active(status, APP_NAME_SERVER, APP_NAME_ADMIN),
            timeout=300,
        )

        juju.integrate(f"{APP_NAME}:ui", f"{APP_NAME_SERVER}:ui")
        juju.integrate(f"{APP_NAME}:temporal-host-info", f"{APP_NAME_SERVER}:temporal-host-info")

        juju.wait(
            lambda status: jubilant.all_active(status, APP_NAME),
            timeout=300,
        )

        juju.integrate(f"{APP_NAME}:nginx-route", "nginx-ingress-integrator:nginx-route")

        juju.wait(
            lambda status: jubilant.all_active(status, APP_NAME, "nginx-ingress-integrator"),
            timeout=300,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for Temporal UI charm."""

    def test_basic_client(self, juju: jubilant.Juju):
        """Perform GET request on the Temporal UI host."""
        url = f"http://{unit_status(juju, APP_NAME).address}:8080"
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300)
        assert response.status_code == 200

    def test_ingress(self, juju: jubilant.Juju):
        """Set external-hostname and test connectivity through ingress."""
        new_hostname = "temporal-web"
        juju.config(APP_NAME, {"external-hostname": new_hostname})

        with fast_forward(juju):
            juju.wait(
                lambda status: jubilant.all_active(status, APP_NAME, "nginx-ingress-integrator"),
                successes=30,
                timeout=1200,
            )
            result = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "ingress-nginx",
                    "get",
                    "svc",
                    "ingress-nginx-controller",
                    "-o",
                    "jsonpath={.status.loadBalancer.ingress[0].ip}",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            ingress_ip = result.stdout.strip()

            with unittest.mock.patch.multiple(socket, getaddrinfo=gen_patch_getaddrinfo(new_hostname, ingress_ip)):
                response = requests.get(
                    f"https://{ingress_ip}",
                    headers={"Host": new_hostname},
                    timeout=10,
                    verify=False,  # nosec
                )
                assert response.status_code == 200 and 'id="svelte"' in response.text.lower()

    def test_restart_action(self, juju: jubilant.Juju):
        """Test charm restart action."""
        juju.run(f"{APP_NAME}/0", "restart")

        with fast_forward(juju):
            juju.wait(
                lambda status: jubilant.all_active(status, APP_NAME),
                timeout=600,
            )

    def test_scaling_up(self, juju: jubilant.Juju):
        """Scale Temporal worker charm up to 2 units."""
        scale(juju, app=APP_NAME, units=2)

    def test_host_info_relation(self, juju: jubilant.Juju):
        """Test that the server address from the host-info relation is used in charm config."""
        server_address = unit_status(juju, APP_NAME_SERVER).address

        stdout = juju.ssh(
            f"{APP_NAME}/0",
            "cat /home/ui-server/config/charm.yaml",
            container="temporal-ui",
        )
        charm_config = yaml.safe_load(stdout)
        # FIXME: change port back to 7233 when canonical/temporal-k8s-operator#152 is resolved
        assert charm_config["temporalGrpcAddress"] == f"{server_address}:7236"

    def test_host_info_relation_removed_causes_blocked(self, juju: jubilant.Juju):
        """Test that removing the host-info relation causes the charm to go blocked."""
        juju.remove_relation(
            f"{APP_NAME}:temporal-host-info",
            f"{APP_NAME_SERVER}:temporal-host-info",
        )
        with fast_forward(juju):
            juju.wait(
                lambda status: jubilant.all_blocked(status, APP_NAME),
                timeout=300,
            )

        # Re-integrate so subsequent tests still have an active charm.
        juju.integrate(f"{APP_NAME}:temporal-host-info", f"{APP_NAME_SERVER}:temporal-host-info")
        with fast_forward(juju):
            juju.wait(
                lambda status: jubilant.all_active(status, APP_NAME),
                timeout=300,
            )
