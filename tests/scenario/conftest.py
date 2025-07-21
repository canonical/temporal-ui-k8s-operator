# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import json

import ops.testing
import pytest

from charm import TemporalUiK8SOperatorCharm


def pytest_configure(config):  # noqa: DCO020
    """Flags that can be configured to modify fixture behavior.

    Used to determine how _state in the peer relation app databag is populated.

    Args:
        config: the pytest config object
    """
    config.addinivalue_line("markers", "peer_relation_uninitialized")


@pytest.fixture
def external_hostname():
    return "new-temporal-ui-k8s"


@pytest.fixture
def tls_secret_name():
    return "new-tls"


@pytest.fixture
def config(external_hostname, tls_secret_name):
    return {
        "external-hostname": external_hostname,
        "tls-secret-name": tls_secret_name,
    }


@pytest.fixture
def config_with_auth_enabled(config):
    return {
        **config,
        "auth-enabled": True,
        "auth-provider-url": "some-provider-url",
        "auth-client-id": "some-client-id",
        "auth-client-secret": "some-client-secret",
    }


@pytest.fixture
def temporal_ui_k8s_charm():
    yield TemporalUiK8SOperatorCharm


@pytest.fixture(scope="function")
def context(temporal_ui_k8s_charm):
    return ops.testing.Context(charm_type=temporal_ui_k8s_charm)


@pytest.fixture(scope="function")
def temporal_ui_container():
    return ops.testing.Container(
        "temporal-ui",
        can_connect=True,
    )


@pytest.fixture(scope="function")
def temporal_ui_container_initialized():
    return ops.testing.Container(
        "temporal-ui",
        can_connect=True,
        check_infos=[ops.testing.CheckInfo("up", status=ops.pebble.CheckStatus.UP)],
        layers={
            "initialized-layer": ops.pebble.Layer(
                {
                    "checks": {
                        "up": ops.pebble.CheckDict(
                            exec=ops.pebble.HttpDict(
                                url="http://localhost:8080/",
                            ),
                            level=None,
                            period="10s",
                            override="replace",
                            startup=ops.pebble.CheckStartup.ENABLED,
                            threshold=3,
                        ),
                    },
                    "services": {
                        "temporal-ui": {
                            "on-check-failure": {
                                "up": "ignore",
                            },
                        },
                    },
                },
            ),
        },
    )


@pytest.fixture(scope="function")
def peer_relation(request):
    if request.node.get_closest_marker("peer_relation_uninitialized"):
        return ops.testing.PeerRelation(endpoint="peer")

    state_data = {
        "server_status": json.dumps("ready"),
    }

    return ops.testing.PeerRelation(endpoint="peer", local_app_data=state_data)


@pytest.fixture(scope="function")
def ui_relation():
    return ops.testing.Relation("ui", remote_app_data={"server_status": "ready"})


@pytest.fixture(scope="function")
def traefik_ingress_relation():
    return ops.testing.Relation("ingress")


@pytest.fixture(scope="function")
def nginx_relation():
    return ops.testing.Relation("nginx-route")
