# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
import unittest.mock

import ops
import ops.testing
import pytest

logger = logging.getLogger(__name__)

UI_PORT = "8080"


def test_smoke(context, state):
    context.run(context.on.start(), state)


@pytest.mark.peer_relation_uninitialized
def test_blocked_by_temporal_server(context, state, temporal_ui_container, all_required_relations, ui_relation):
    all_required_relations.remove(ui_relation)
    state = dataclasses.replace(state, relations=all_required_relations)

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    assert state_out.unit_status == ops.BlockedStatus("ui:temporal relation: not available")


def test_blocked_when_host_info_absent(
    context, state, temporal_ui_container, all_required_relations, host_info_relation
):
    all_required_relations.remove(host_info_relation)
    state = dataclasses.replace(state, relations=all_required_relations)

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    assert state_out.unit_status == ops.BlockedStatus("temporal-host-info relation not established")


def test_blocked_by_peer_relation_not_ready(
    context, state, temporal_ui_container, all_required_relations, peer_relation
):
    all_required_relations.remove(peer_relation)
    state = dataclasses.replace(state, relations=all_required_relations)

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    assert state_out.get_container("temporal-ui").plan.to_dict() == {}
    assert state_out.unit_status == ops.BlockedStatus("peer relation not ready")


def test_ingress(
    context,
    nginx_state,
    temporal_ui_container,
    config,
    temporal_ui_container_initialized,
    ui_relation,
    nginx_relation,
    external_hostname,
    tls_secret_name,
):
    state = dataclasses.replace(nginx_state, config={})

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    with context(context.on.config_changed(), state_out) as manager:
        manager.charm._require_nginx_route()

        assert state_out.get_relation(nginx_relation.id).local_app_data == {
            "service-namespace": manager.charm.model.name,
            "service-hostname": manager.charm.app.name,
            "service-name": manager.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": "temporal-tls",
            "backend-protocol": "HTTP",
        }

    temp_config = {
        "external-hostname": config["external-hostname"],
    }
    state_out = dataclasses.replace(state_out, config=temp_config)

    with context(context.on.config_changed(), state_out) as manager:
        state_out = manager.run()

        manager.charm._require_nginx_route()

        assert state_out.get_relation(nginx_relation.id).local_app_data == {
            "service-namespace": manager.charm.model.name,
            "service-hostname": external_hostname,
            "service-name": manager.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": "temporal-tls",
            "backend-protocol": "HTTP",
        }

    state_out = dataclasses.replace(state_out, config=config, containers=[temporal_ui_container_initialized])

    with context(context.on.config_changed(), state_out) as manager:
        state_out = manager.run()

        manager.charm._require_nginx_route()

        assert state_out.get_relation(nginx_relation.id).local_app_data == {
            "service-namespace": manager.charm.model.name,
            "service-hostname": external_hostname,
            "service-name": manager.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": tls_secret_name,
            "backend-protocol": "HTTP",
        }


def test_ready(context, state, temporal_ui_container, temporal_ui_container_initialized, ui_relation):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    assert state_out.get_container("temporal-ui").plan.to_dict() == {
        "services": {
            "temporal-ui": {
                "summary": "temporal ui",
                "command": "ui-server --root /home/ui-server --env charm start",
                "startup": "enabled",
                "override": "replace",
                "environment": {
                    "LOG_LEVEL": "info",
                    "TEMPORAL_UI_PORT": 8080,
                    "TEMPORAL_ADDRESS": "10.0.0.1:7233",
                    "TEMPORAL_DEFAULT_NAMESPACE": "default",
                    "TEMPORAL_AUTH_ENABLED": False,
                    "TEMPORAL_WORKFLOW_CANCEL_DISABLED": False,
                    "TEMPORAL_WORKFLOW_RESET_DISABLED": False,
                    "TEMPORAL_WORKFLOW_SIGNAL_DISABLED": False,
                    "TEMPORAL_WORKFLOW_TERMINATE_DISABLED": False,
                    "TEMPORAL_START_WORKFLOW_DISABLED": False,
                    "TEMPORAL_HIDE_WORKFLOW_QUERY_ERRORS": False,
                    "TEMPORAL_CODEC_ENDPOINT": "",
                    "TEMPORAL_CODEC_PASS_ACCESS_TOKEN": False,  # nosec B105
                    "TEMPORAL_BATCH_ACTIONS_DISABLED": False,
                },
                "on-check-failure": {"up": "ignore"},
            }
        },
        "checks": {
            "up": {
                "http": {"url": "http://localhost:8080/"},
                "override": "replace",
                "period": "10s",
            }
        },
    }

    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == ops.pebble.ServiceStatus.ACTIVE


@pytest.mark.parametrize("value", [True, False])
def test_start_workflow_disabled_config(
    context,
    state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    ui_relation,
    value,
):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)
    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    state_out = dataclasses.replace(
        state_out,
        config={"start-workflow-disabled": value},
        containers=[temporal_ui_container_initialized],
    )

    with context(context.on.config_changed(), state_out) as manager:
        state_out = manager.run()

        env = state_out.get_container("temporal-ui").plan.to_dict()["services"]["temporal-ui"]["environment"]
        assert env["TEMPORAL_START_WORKFLOW_DISABLED"] == value

        config = manager.charm.unit.get_container("temporal-ui").pull("/home/ui-server/config/charm.yaml").read()
        assert f"startWorkflowDisabled: {value}" in config


def test_auth(
    context,
    nginx_state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    ui_relation,
    config_with_auth_enabled,
    external_hostname,
):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), nginx_state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    state_out = dataclasses.replace(state_out, config=config_with_auth_enabled)

    assert sorted(state_out.get_container("temporal-ui").plan.to_dict()) == sorted(
        {
            "services": {
                "temporal-ui": {
                    "summary": "temporal ui",
                    "command": "ui-server --root /home/ui-server --env charm start",
                    "startup": "enabled",
                    "override": "replace",
                    "environment": {
                        "LOG_LEVEL": "info",
                        "TEMPORAL_UI_PORT": 8080,
                        "TEMPORAL_ADDRESS": "10.0.0.1:7233",
                        "TEMPORAL_DEFAULT_NAMESPACE": "default",
                        "TEMPORAL_AUTH_ENABLED": True,
                        "TEMPORAL_AUTH_PROVIDER_URL": "some-provider-url",
                        "TEMPORAL_AUTH_CLIENT_ID": "some-client-id",
                        "TEMPORAL_AUTH_CLIENT_SECRET": "some-client-secret",  # nosec B105
                        "TEMPORAL_AUTH_SCOPES": "[openid,profile,email]",
                        "TEMPORAL_AUTH_CALLBACK_URL": f"https://{external_hostname}/auth/sso/callback",
                        "TEMPORAL_WORKFLOW_CANCEL_DISABLED": False,
                        "TEMPORAL_WORKFLOW_RESET_DISABLED": False,
                        "TEMPORAL_WORKFLOW_SIGNAL_DISABLED": False,
                        "TEMPORAL_WORKFLOW_TERMINATE_DISABLED": False,
                        "TEMPORAL_START_WORKFLOW_DISABLED": False,
                        "TEMPORAL_HIDE_WORKFLOW_QUERY_ERRORS": False,
                        "TEMPORAL_CODEC_ENDPOINT": "",
                        "TEMPORAL_CODEC_PASS_ACCESS_TOKEN": False,  # nosec B105
                        "TEMPORAL_BATCH_ACTIONS_DISABLED": False,
                    },
                    "on-check-failure": {"up": "ignore"},
                }
            },
            "checks": {
                "up": {
                    "http": {"url": "http://localhost:8080/"},
                    "override": "replace",
                    "period": "10s",
                }
            },
        }
    )

    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == ops.pebble.ServiceStatus.ACTIVE


def test_update_status_up(context, state, temporal_ui_container, temporal_ui_container_initialized, ui_relation):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])

    state_out = context.run(context.on.update_status(), state_out)

    assert state_out.unit_status == ops.ActiveStatus()


def test_update_status_down(context, state, temporal_ui_container, temporal_ui_container_initialized, ui_relation):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    temporal_container_down = dataclasses.replace(
        temporal_ui_container_initialized, check_infos=[ops.testing.CheckInfo("up", status=ops.pebble.CheckStatus.DOWN)]
    )
    state_out = dataclasses.replace(state_out, containers=[temporal_container_down])

    state_out = context.run(context.on.update_status(), state_out)

    assert state_out.unit_status == ops.MaintenanceStatus("Status check: DOWN")


def test_incomplete_pebble_plan(context, state, temporal_ui_container, temporal_ui_container_initialized, ui_relation):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    incomplete_pebble_plan = {"services": {"temporal-ui": {"override": "replace"}}}
    incomplete_pebble_plan_with_checks = {
        **incomplete_pebble_plan,
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
    }

    temporal_ui_container_incomplete = dataclasses.replace(
        temporal_ui_container_initialized,
        layers={
            "incomplete-layer": ops.pebble.Layer(incomplete_pebble_plan_with_checks),
        },
    )
    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_incomplete])

    state_out = context.run(context.on.update_status(), state_out)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert sorted(state_out.get_container("temporal-ui").plan.to_dict()) != sorted(incomplete_pebble_plan)


def test_missing_pebble_plan(context, state, temporal_ui_container, temporal_ui_container_initialized, ui_relation):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    with unittest.mock.patch("charm.TemporalUiK8SOperatorCharm._validate_pebble_plan", return_value=False):
        state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])

        state_out = context.run(context.on.update_status(), state_out)

        assert state_out.unit_status == ops.MaintenanceStatus("replanning application")

        assert state_out.get_container("temporal-ui").plan.to_dict() is not None


def test_blocked_on_two_ingresses(
    context,
    nginx_state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    traefik_relation,
):
    state = dataclasses.replace(nginx_state, relations=nginx_state.relations | {traefik_relation})
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(traefik_relation), state_out)

    assert state_out.unit_status == ops.BlockedStatus(
        "Only one ingress solution is allowed - remove the ingress or the nginx-route relation"
    )


def test_traefik_ingress_ready(
    context,
    traefik_state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    ui_relation,
):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), traefik_state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == ops.pebble.ServiceStatus.ACTIVE


CA_1 = "-----BEGIN CERTIFICATE-----\nCA-ONE\n-----END CERTIFICATE-----"
CA_2 = "-----BEGIN CERTIFICATE-----\nCA-TWO\n-----END CERTIFICATE-----"
CA_PATH = "/home/ui-server/certs/ca.pem"
CONFIG_PATH = "/home/ui-server/config/charm.yaml"


def make_ca_relation(*certificates):
    """Return a receive-ca-cert relation carrying the given CA certificates.

    Args:
        certificates: PEM-encoded CA certificates published by the provider.

    Returns:
        The relation, with the certificates in the provider's app databag.
    """
    return ops.testing.Relation(
        "receive-ca-cert",
        remote_app_data={"certificates": json.dumps(list(certificates))},
    )


def _tls_state(state, peer_relation, ui_relation, tls_host_info_relation, *extra_relations):
    """Return a state whose temporal-host-info provider reports a TLS frontend.

    Args:
        state: The base state.
        peer_relation: The peer relation.
        ui_relation: The ui:temporal relation.
        tls_host_info_relation: A temporal-host-info relation reporting tls=true.
        extra_relations: Any further relations to include.

    Returns:
        The state with those relations.
    """
    return dataclasses.replace(state, relations=[peer_relation, ui_relation, tls_host_info_relation, *extra_relations])


def _read(context, state_out, path):
    """Read a file the charm pushed into the temporal-ui container.

    Args:
        context: The scenario context the charm ran in.
        state_out: The state after the charm ran.
        path: Absolute path of the file in the container.

    Returns:
        The file's contents.
    """
    root = state_out.get_container("temporal-ui").get_filesystem(context)
    return (root / path.lstrip("/")).read_text()


def test_plaintext_frontend_has_no_tls_config(
    context, state, temporal_ui_container, temporal_ui_container_initialized, ui_relation
):
    # Without the tls signal the UI keeps dialling plaintext: no tls block and
    # no TLS settings in the environment.
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)
    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    environment = state_out.get_container("temporal-ui").plan.services["temporal-ui"].environment
    assert "TEMPORAL_TLS_CA" not in environment
    assert "tls:" not in _read(context, state_out, CONFIG_PATH)


def test_tls_frontend_without_ca_blocks(
    context, state, temporal_ui_container, peer_relation, ui_relation, tls_host_info_relation
):
    # A TLS frontend can't be verified without its CA, so the charm blocks with
    # an actionable message instead of starting a UI that can't connect.
    state = _tls_state(state, peer_relation, ui_relation, tls_host_info_relation)

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    assert state_out.unit_status == ops.BlockedStatus(
        "Temporal frontend uses TLS: integrate receive-ca-cert with its CA"
    )


def test_tls_frontend_with_ca_configures_ui(
    context,
    state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    peer_relation,
    ui_relation,
    tls_host_info_relation,
):
    ca_relation = make_ca_relation(CA_1)
    state = _tls_state(state, peer_relation, ui_relation, tls_host_info_relation, ca_relation)

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)
    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    environment = state_out.get_container("temporal-ui").plan.services["temporal-ui"].environment
    assert environment["TEMPORAL_ADDRESS"] == "temporal-k8s.test.svc.cluster.local:7233"
    assert environment["TEMPORAL_TLS_CA"] == CA_PATH
    assert environment["TEMPORAL_TLS_ENABLE_HOST_VERIFICATION"] == "true"
    assert _read(context, state_out, CA_PATH) == CA_1
    config = _read(context, state_out, CONFIG_PATH)
    assert f"tls:\n  caFile: {CA_PATH}\n  enableHostVerification: true\n" in config


def test_ca_rotation_restarts_ui(
    context,
    state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    peer_relation,
    ui_relation,
    tls_host_info_relation,
):
    # The CA path never changes, so a rotated CA must change the environment
    # (via its hash) for Pebble to restart ui-server, which reads it only at
    # startup.
    def ca_hash(*certificates):
        """Run the charm with the given CA certificates and return the bundle hash.

        Args:
            certificates: PEM-encoded CA certificates published by the provider.

        Returns:
            The TEMPORAL_TLS_CA_HASH value from the Pebble environment.
        """
        ca_relation = make_ca_relation(*certificates)
        tls_state = _tls_state(state, peer_relation, ui_relation, tls_host_info_relation, ca_relation)
        state_out = context.run(context.on.pebble_ready(temporal_ui_container), tls_state)
        state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
        state_out = context.run(context.on.relation_changed(ui_relation), state_out)
        return state_out.get_container("temporal-ui").plan.services["temporal-ui"].environment["TEMPORAL_TLS_CA_HASH"]

    assert ca_hash(CA_1) != ca_hash(CA_2)
    # The library returns an unordered set; the bundle must not depend on it.
    assert ca_hash(CA_1, CA_2) == ca_hash(CA_2, CA_1)


def test_ca_removed_blocks(
    context,
    state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    peer_relation,
    ui_relation,
    tls_host_info_relation,
):
    ca_relation = make_ca_relation(CA_1)
    state = _tls_state(state, peer_relation, ui_relation, tls_host_info_relation, ca_relation)
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)
    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])

    state_out = context.run(context.on.relation_broken(ca_relation), state_out)

    assert state_out.unit_status == ops.BlockedStatus(
        "Temporal frontend uses TLS: integrate receive-ca-cert with its CA"
    )
