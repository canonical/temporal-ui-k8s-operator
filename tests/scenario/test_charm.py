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


SERVICE = ops.pebble.ServiceStatus


def _spy_on(method_name):
    """Spy on a method of ops.Container, keeping its behaviour.

    Args:
        method_name: Name of the ops.Container method to spy on.

    Returns:
        The patcher, to be used as a context manager yielding the mock.
    """
    return unittest.mock.patch.object(
        ops.model.Container, method_name, autospec=True, side_effect=getattr(ops.model.Container, method_name)
    )


def _reconciled(context, state, temporal_ui_container):
    """Run the charm until the workload is planned and running.

    The state is returned ready to be run again: the check infos Pebble reports are dropped, as the
    consistency checker of the testing framework does not accept their default threshold.

    Args:
        context: The scenario context.
        state: The base state.
        temporal_ui_container: The workload container to start from.

    Returns:
        The state after the workload was planned.
    """
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)
    container = state_out.get_container("temporal-ui")
    return dataclasses.replace(state_out, containers=[dataclasses.replace(container, check_infos=frozenset())])


def test_install_announces_installation_whatever_the_relations(context, state):
    state = dataclasses.replace(state, relations=[])

    state_out = context.run(context.on.install(), state)

    assert state_out.unit_status == ops.MaintenanceStatus("installing temporal ui tools")
    assert state_out.get_container("temporal-ui").plan.to_dict() == {}


@pytest.mark.parametrize("event_name", ["pebble_ready", "config_changed", "upgrade_charm", "update_status"])
def test_container_not_ready_defers_nothing(context, state, event_name):
    container = ops.testing.Container("temporal-ui", can_connect=False)
    state = dataclasses.replace(state, containers=[container], unit_status=ops.ActiveStatus("kept"))
    event = getattr(context.on, event_name)
    event = event(container) if event_name == "pebble_ready" else event()

    state_out = context.run(event, state)

    assert state_out.deferred == []
    if event_name == "config_changed":
        assert state_out.unit_status == ops.WaitingStatus("configuring temporal")
    else:
        assert state_out.unit_status == ops.ActiveStatus("kept")


def test_config_changed_announces_configuration(context, state, temporal_ui_container):
    state_out = context.run(context.on.config_changed(), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == SERVICE.ACTIVE


def test_config_changed_blocks_when_invalid(context, state):
    state = dataclasses.replace(state, relations=[])

    state_out = context.run(context.on.config_changed(), state)

    assert state_out.unit_status == ops.BlockedStatus("peer relation not ready")


def test_upgrade_charm_reconciles(context, state):
    state_out = context.run(context.on.upgrade_charm(), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert "temporal-ui" in state_out.get_container("temporal-ui").plan.services


def test_peer_relation_changed_reconciles(context, state, peer_relation):
    state_out = context.run(context.on.relation_changed(peer_relation), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")


@pytest.mark.parametrize("leader", [True, False])
def test_ui_relation_joined_announces_the_change_while_waiting_for_pebble(context, state, ui_relation, leader):
    container = ops.testing.Container("temporal-ui", can_connect=False)
    state = dataclasses.replace(state, containers=[container], leader=leader)

    state_out = context.run(context.on.relation_joined(ui_relation), state)

    assert state_out.unit_status == ops.WaitingStatus("handling ui change")
    assert state_out.deferred == []


def test_ui_relation_changed_keeps_status_while_waiting_for_pebble(context, state, ui_relation):
    container = ops.testing.Container("temporal-ui", can_connect=False)
    state = dataclasses.replace(state, containers=[container], unit_status=ops.ActiveStatus())

    state_out = context.run(context.on.relation_changed(ui_relation), state)

    assert state_out.unit_status == ops.ActiveStatus()
    assert state_out.deferred == []


@pytest.mark.peer_relation_uninitialized
@pytest.mark.parametrize("event_name", ["relation_joined", "relation_changed", "relation_broken"])
def test_ui_relation_events_keep_status_without_peer_relation(context, state, all_required_relations, event_name):
    ui = next(relation for relation in all_required_relations if relation.endpoint == "ui")
    peer = next(relation for relation in all_required_relations if relation.endpoint == "peer")
    state = dataclasses.replace(state, relations=[ui], unit_status=ops.ActiveStatus("kept"))
    assert peer not in state.relations

    state_out = context.run(getattr(context.on, event_name)(ui), state)

    assert state_out.unit_status == ops.ActiveStatus("kept")
    assert state_out.deferred == []
    assert state_out.get_container("temporal-ui").plan.to_dict() == {}


@pytest.mark.parametrize("leader", [True, False])
def test_ui_relation_broken_blocks(context, state, temporal_ui_container, ui_relation, leader):
    state = dataclasses.replace(state, leader=leader)
    state_out = _reconciled(context, state, temporal_ui_container)

    state_out = context.run(context.on.relation_broken(ui_relation), state_out)

    assert state_out.unit_status == ops.BlockedStatus("ui:temporal relation: not available")


def test_ui_relation_server_not_ready_blocks(context, state, temporal_ui_container, peer_relation):
    ui = ops.testing.Relation("ui", remote_app_data={"server_status": "blocked"})
    state = dataclasses.replace(state, relations=[peer_relation, ui, *_host_info(state)])

    state_out = context.run(context.on.relation_changed(ui), state)

    assert state_out.unit_status == ops.BlockedStatus("ui:temporal relation: server is not ready")
    assert state_out.get_container("temporal-ui").plan.to_dict() == {}


def _host_info(state):
    """Return the temporal-host-info relations of a state.

    Args:
        state: The state.

    Returns:
        The temporal-host-info relations.
    """
    return [relation for relation in state.relations if relation.endpoint == "temporal-host-info"]


def test_server_status_is_read_from_the_ui_relation_not_the_peer_relation(
    context, state, temporal_ui_container, host_info_relation
):
    # The peer relation holds a stale value; the source of truth is the ui:temporal relation.
    peer = ops.testing.PeerRelation(endpoint="peer", local_app_data={"server_status": json.dumps("blocked")})
    ui = ops.testing.Relation("ui", remote_app_data={"server_status": "ready"})
    state = dataclasses.replace(state, relations=[peer, ui, host_info_relation])

    state_out = context.run(context.on.relation_changed(ui), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")


def test_server_status_is_not_cached_in_the_peer_relation_when_missing(
    context, state, host_info_relation, peer_relation
):
    ui = ops.testing.Relation("ui")
    peer = ops.testing.PeerRelation(endpoint="peer")
    state = dataclasses.replace(state, relations=[peer, ui, host_info_relation])

    state_out = context.run(context.on.relation_changed(ui), state)

    assert state_out.unit_status == ops.BlockedStatus("ui:temporal relation: server is not ready")
    assert state_out.get_relation(peer.id).local_app_data == {}


@pytest.mark.parametrize("event_name", ["relation_joined", "relation_changed", "config_changed"])
def test_leader_keeps_the_legacy_server_status_of_the_peer_relation_up_to_date(
    context, state, host_info_relation, event_name
):
    # Units that still run a previous revision read the JSON encoded status from the peer relation.
    peer = ops.testing.PeerRelation(endpoint="peer", local_app_data={"other-key": "kept"})
    ui = ops.testing.Relation("ui", remote_app_data={"server_status": "ready"})
    state = dataclasses.replace(state, relations=[peer, ui, host_info_relation])
    event = getattr(context.on, event_name)
    event = event(ui) if event_name != "config_changed" else event()

    state_out = context.run(event, state)

    assert state_out.get_relation(peer.id).local_app_data == {
        "other-key": "kept",
        "server_status": json.dumps("ready"),
    }


def test_leader_updates_the_legacy_server_status_when_the_server_changes(context, state, host_info_relation):
    peer = ops.testing.PeerRelation(endpoint="peer", local_app_data={"server_status": json.dumps("ready")})
    ui = ops.testing.Relation("ui", remote_app_data={"server_status": "blocked"})
    state = dataclasses.replace(state, relations=[peer, ui, host_info_relation])

    state_out = context.run(context.on.relation_changed(ui), state)

    assert state_out.get_relation(peer.id).local_app_data == {"server_status": json.dumps("blocked")}


def test_leader_marks_the_legacy_server_status_blocked_when_the_ui_relation_is_broken(
    context, state, temporal_ui_container, peer_relation, ui_relation
):
    state_out = context.run(context.on.relation_broken(ui_relation), state)

    assert state_out.get_relation(peer_relation.id).local_app_data == {"server_status": json.dumps("blocked")}

    # The value of the previous revisions is not cleaned up by the events that follow.
    remaining = [relation for relation in state_out.relations if relation.endpoint != "ui"]
    state_out = context.run(context.on.update_status(), dataclasses.replace(state_out, relations=remaining))
    assert state_out.get_relation(peer_relation.id).local_app_data == {"server_status": json.dumps("blocked")}


def test_non_leader_reads_the_relation_and_writes_nothing_to_the_peer_relation(
    context, state, temporal_ui_container, peer_relation, ui_relation
):
    state = dataclasses.replace(state, leader=False)

    state_out = context.run(context.on.relation_changed(ui_relation), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert state_out.get_relation(peer_relation.id).local_app_data == peer_relation.local_app_data


def test_reconcile_is_idempotent(context, state, temporal_ui_container, tmp_path):
    container = dataclasses.replace(
        temporal_ui_container,
        mounts={"ui-server": ops.testing.Mount(location="/home/ui-server", source=tmp_path)},
    )
    state = dataclasses.replace(state, containers=[container])
    state_out = _reconciled(context, state, container)
    plan = state_out.get_container("temporal-ui").plan.to_dict()
    config = (tmp_path / "config" / "charm.yaml").read_text()

    with _spy_on("replan") as replan, _spy_on("push") as push:
        state_again = context.run(context.on.config_changed(), state_out)

    assert state_again.unit_status == ops.MaintenanceStatus("replanning application")
    assert state_again.get_container("temporal-ui").plan.to_dict() == plan
    assert (tmp_path / "config" / "charm.yaml").read_text() == config
    replan.assert_not_called()
    push.assert_not_called()


def test_config_change_replans_and_rewrites_the_config(context, state, temporal_ui_container, tmp_path):
    container = dataclasses.replace(
        temporal_ui_container,
        mounts={"ui-server": ops.testing.Mount(location="/home/ui-server", source=tmp_path)},
    )
    state = dataclasses.replace(state, containers=[container])
    state_out = _reconciled(context, state, container)

    with _spy_on("replan") as replan:
        state_out = context.run(
            context.on.config_changed(), dataclasses.replace(state_out, config={"start-workflow-disabled": True})
        )

    replan.assert_called_once()
    environment = state_out.get_container("temporal-ui").plan.services["temporal-ui"].environment
    assert environment["TEMPORAL_START_WORKFLOW_DISABLED"] is True
    assert "startWorkflowDisabled: True" in (tmp_path / "config" / "charm.yaml").read_text()


def test_stopped_service_is_started_again(context, state, temporal_ui_container):
    state_out = _reconciled(context, state, temporal_ui_container)
    container = state_out.get_container("temporal-ui")
    stopped = dataclasses.replace(container, service_statuses={"temporal-ui": SERVICE.INACTIVE})

    state_out = context.run(context.on.config_changed(), dataclasses.replace(state_out, containers=[stopped]))

    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == SERVICE.ACTIVE
    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")


def test_update_status_blocks_when_invalid(context, state, temporal_ui_container_initialized):
    state = dataclasses.replace(state, relations=[], containers=[temporal_ui_container_initialized])

    state_out = context.run(context.on.update_status(), state)

    assert state_out.unit_status == ops.BlockedStatus("peer relation not ready")


def test_update_status_does_not_replan_when_the_plan_is_in_place(context, state, temporal_ui_container_initialized):
    state = dataclasses.replace(state, containers=[temporal_ui_container_initialized])

    with _spy_on("replan") as replan:
        state_out = context.run(context.on.update_status(), state)

    replan.assert_not_called()
    assert state_out.unit_status == ops.ActiveStatus()


def test_update_status_reports_auth_enabled(
    context, nginx_state, temporal_ui_container_initialized, config_with_auth_enabled
):
    state = dataclasses.replace(
        nginx_state, config=config_with_auth_enabled, containers=[temporal_ui_container_initialized]
    )

    state_out = context.run(context.on.update_status(), state)

    assert state_out.unit_status == ops.ActiveStatus("auth enabled")
    assert state_out.workload_version == "2.27.1"


def test_update_status_replans_an_incomplete_plan_and_keeps_the_service_running(
    context, state, temporal_ui_container, temporal_ui_container_initialized
):
    state_out = _reconciled(context, state, temporal_ui_container)
    plan = state_out.get_container("temporal-ui").plan.to_dict()
    incomplete = dataclasses.replace(
        temporal_ui_container_initialized,
        check_infos=frozenset(),
        layers={"incomplete-layer": ops.pebble.Layer({"services": {"temporal-ui": {"override": "replace"}}})},
    )

    state_out = context.run(context.on.update_status(), dataclasses.replace(state_out, containers=[incomplete]))

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert state_out.get_container("temporal-ui").plan.to_dict()["services"] == plan["services"]


def test_update_status_keeps_status_while_waiting_for_pebble(context, state):
    container = ops.testing.Container("temporal-ui", can_connect=False)
    state = dataclasses.replace(
        state, containers=[container], unit_status=ops.MaintenanceStatus("replanning application")
    )

    state_out = context.run(context.on.update_status(), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    assert state_out.deferred == []


def test_host_info_change_reconciles(context, state, temporal_ui_container, host_info_relation):
    state_out = context.run(context.on.relation_changed(host_info_relation), state)

    assert state_out.unit_status == ops.MaintenanceStatus("replanning application")
    environment = state_out.get_container("temporal-ui").plan.services["temporal-ui"].environment
    assert environment["TEMPORAL_ADDRESS"] == "10.0.0.1:7233"


def test_host_info_removal_blocks(context, state, temporal_ui_container, host_info_relation):
    state_out = _reconciled(context, state, temporal_ui_container)

    state_out = context.run(context.on.relation_broken(host_info_relation), state_out)

    assert state_out.unit_status == ops.BlockedStatus("temporal-host-info relation not established")


def test_auth_requires_its_parameters(context, nginx_state, config):
    state = dataclasses.replace(nginx_state, config={**config, "auth-enabled": True})

    state_out = context.run(context.on.config_changed(), state)

    assert state_out.unit_status == ops.BlockedStatus("Invalid config: auth-client-id value missing")


def test_auth_requires_an_ingress(context, state, config_with_auth_enabled):
    state = dataclasses.replace(state, config=config_with_auth_enabled)

    state_out = context.run(context.on.config_changed(), state)

    assert state_out.unit_status == ops.BlockedStatus("Invalid config: auth cannot work without ingress relation")


def test_restart_action(context, state, temporal_ui_container):
    state_out = _reconciled(context, state, temporal_ui_container)

    state_out = context.run(context.on.action("restart"), state_out)

    assert context.action_results == {"result": "worker successfully restarted"}
    assert state_out.unit_status == ops.MaintenanceStatus("restarting ui")
    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == SERVICE.ACTIVE


def test_restart_action_fails_while_waiting_for_pebble(context, state):
    container = ops.testing.Container("temporal-ui", can_connect=False)
    state = dataclasses.replace(state, containers=[container])

    with pytest.raises(ops.testing.ActionFailed):
        context.run(context.on.action("restart"), state)

    assert context.action_results is None or context.action_results == {}
