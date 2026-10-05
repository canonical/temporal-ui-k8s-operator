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
from helpers import (
    fast_forward,
    gen_patch_getaddrinfo,
    host_info_app_data,
    scale,
    unit_status,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(pathlib.Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

APP_NAME_SERVER = "temporal-k8s"
APP_NAME_ADMIN = "temporal-admin-k8s"

NGINX_INGRESS_INTEGRATOR_CHANNEL = "latest/edge"
SELF_SIGNED_CERTIFICATES_CHANNEL = "1/stable"
TEMPORAL_FRONTEND_PORT = "7233"


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
        host_info = host_info_app_data(juju, APP_NAME)

        charm_config = yaml.safe_load(
            juju.ssh(f"{APP_NAME}/0", "cat /home/ui-server/config/charm.yaml", container="temporal-ui")
        )
        assert charm_config["temporalGrpcAddress"] == f"{host_info['host']}:{host_info['port']}"

    def test_namespaces_api(self, juju: jubilant.Juju):
        """Test the UI reaches the Temporal frontend over plaintext gRPC.

        `GET /api/v1/namespaces` makes the UI server dial the frontend, so it
        fails with HTTP 503 when the UI can't talk to it.
        """
        url = f"http://{unit_status(juju, APP_NAME).address}:8080/api/v1/namespaces"
        response = requests.get(url, timeout=60)
        assert response.status_code == 200, response.text

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

    def test_tls_frontend(self, juju: jubilant.Juju):
        """Test the UI reaches a TLS frontend once it has the frontend's CA.

        This is the repro from canonical/temporal-k8s-operator#152: with frontend
        certificates related, a UI that dials plaintext gets HTTP 503 from
        `GET /api/v1/namespaces`. It needs a temporal-k8s that publishes the
        frontend port and its TLS state over temporal-host-info, so it is
        skipped against releases that still publish internal-frontend.
        """
        if host_info_app_data(juju, APP_NAME).get("port") != TEMPORAL_FRONTEND_PORT:
            pytest.skip(
                "temporal-k8s on this channel does not publish the frontend over temporal-host-info "
                "yet (canonical/temporal-k8s-operator#152)"
            )

        juju.deploy("self-signed-certificates", channel=SELF_SIGNED_CERTIFICATES_CHANNEL)
        juju.integrate(f"{APP_NAME_SERVER}:frontend-certificates", "self-signed-certificates:certificates")

        # The frontend now serves TLS; without the CA the UI must block rather
        # than keep dialling plaintext.
        with fast_forward(juju):
            juju.wait(
                lambda status: jubilant.all_blocked(status, APP_NAME)
                and host_info_app_data(juju, APP_NAME).get("tls") == "true",
                timeout=900,
            )

        juju.integrate(f"{APP_NAME}:receive-ca-cert", "self-signed-certificates:send-ca-cert")
        with fast_forward(juju):
            juju.wait(lambda status: jubilant.all_active(status, APP_NAME), timeout=600)

        charm_config = yaml.safe_load(
            juju.ssh(f"{APP_NAME}/0", "cat /home/ui-server/config/charm.yaml", container="temporal-ui")
        )
        assert charm_config["tls"]["enableHostVerification"] is True

        url = f"http://{unit_status(juju, APP_NAME).address}:8080/api/v1/namespaces"
        response = requests.get(url, timeout=60)
        assert response.status_code == 200, response.text
