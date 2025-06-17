# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
import unittest.mock

import ops
import ops.testing
import pytest

logger = logging.getLogger(__name__)

UI_PORT = "8080"


@pytest.fixture
def all_required_relations(peer_relation, ui_relation, nginx_relation):
    return [
        peer_relation,
        ui_relation,
        nginx_relation,
    ]


@pytest.fixture
def state(temporal_ui_container, all_required_relations):
    return ops.testing.State(
        leader=True,
        containers=[temporal_ui_container],
        relations=all_required_relations,
    )


def test_smoke(context, state):
    context.run(context.on.start(), state)


@pytest.mark.peer_relation_uninitialized
def test_blocked_by_temporal_server(context, state, temporal_ui_container, all_required_relations, ui_relation):
    all_required_relations.remove(ui_relation)
    state = dataclasses.replace(state, relations=all_required_relations)

    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    assert state_out.unit_status == ops.BlockedStatus("ui:temporal relation: not available")


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
    state,
    temporal_ui_container,
    config,
    temporal_ui_container_initialized,
    ui_relation,
    nginx_relation,
    external_hostname,
    tls_secret_name,
):
    state = dataclasses.replace(state, config={})

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
                "command": "./ui-server --env charm start",
                "startup": "enabled",
                "override": "replace",
                "environment": {
                    "LOG_LEVEL": "info",
                    "TEMPORAL_UI_PORT": 8080,
                    "TEMPORAL_DEFAULT_NAMESPACE": "default",
                    "TEMPORAL_AUTH_ENABLED": False,
                    "TEMPORAL_WORKFLOW_CANCEL_DISABLED": False,
                    "TEMPORAL_WORKFLOW_RESET_DISABLED": False,
                    "TEMPORAL_WORKFLOW_SIGNAL_DISABLED": False,
                    "TEMPORAL_WORKFLOW_TERMINATE_DISABLED": False,
                    "TEMPORAL_HIDE_WORKFLOW_QUERY_ERRORS": False,
                    "TEMPORAL_CODEC_ENDPOINT": "",
                    "TEMPORAL_CODEC_PASS_ACCESS_TOKEN": False,
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


def test_auth(
    context,
    state,
    temporal_ui_container,
    temporal_ui_container_initialized,
    ui_relation,
    config_with_auth_enabled,
    external_hostname,
):
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])
    state_out = context.run(context.on.relation_changed(ui_relation), state_out)

    state_out = dataclasses.replace(state_out, config=config_with_auth_enabled)

    assert sorted(state_out.get_container("temporal-ui").plan.to_dict()) == sorted(
        {
            "services": {
                "temporal-ui": {
                    "summary": "temporal ui",
                    "command": "./ui-server --env charm start",
                    "startup": "enabled",
                    "override": "replace",
                    "environment": {
                        "LOG_LEVEL": "info",
                        "TEMPORAL_UI_PORT": 8080,
                        "TEMPORAL_DEFAULT_NAMESPACE": "default",
                        "TEMPORAL_AUTH_ENABLED": True,
                        "TEMPORAL_AUTH_PROVIDER_URL": "some-provider-url",
                        "TEMPORAL_AUTH_CLIENT_ID": "some-client-id",
                        "TEMPORAL_AUTH_CLIENT_SECRET": "some-client-secret",
                        "TEMPORAL_AUTH_SCOPES": "[openid,profile,email]",
                        "TEMPORAL_AUTH_CALLBACK_URL": f"https://{external_hostname}/auth/sso/callback",
                        "TEMPORAL_WORKFLOW_CANCEL_DISABLED": False,
                        "TEMPORAL_WORKFLOW_RESET_DISABLED": False,
                        "TEMPORAL_WORKFLOW_SIGNAL_DISABLED": False,
                        "TEMPORAL_WORKFLOW_TERMINATE_DISABLED": False,
                        "TEMPORAL_HIDE_WORKFLOW_QUERY_ERRORS": False,
                        "TEMPORAL_CODEC_ENDPOINT": "",
                        "TEMPORAL_CODEC_PASS_ACCESS_TOKEN": False,
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
