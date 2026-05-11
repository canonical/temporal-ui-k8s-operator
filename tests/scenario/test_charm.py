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
    ui_relation,
    nginx_relation,
    external_hostname,
    tls_secret_name,
):
    # In the reconciler, pebble_ready converges fully
    state = dataclasses.replace(nginx_state, config={})

    with context(context.on.pebble_ready(temporal_ui_container), state) as manager:
        state_out = manager.run()

        assert state_out.get_relation(nginx_relation.id).local_app_data == {
            "service-namespace": manager.charm.model.name,
            "service-hostname": manager.charm.app.name,
            "service-name": manager.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": "temporal-tls",
            "backend-protocol": "HTTP",
        }

    # Test with custom hostname
    temp_config = {"external-hostname": config["external-hostname"]}
    state_out = dataclasses.replace(state_out, config=temp_config)

    with context(context.on.config_changed(), state_out) as manager:
        state_out = manager.run()

        assert state_out.get_relation(nginx_relation.id).local_app_data == {
            "service-namespace": manager.charm.model.name,
            "service-hostname": external_hostname,
            "service-name": manager.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": "temporal-tls",
            "backend-protocol": "HTTP",
        }

    # Test with custom tls-secret-name
    state_out = dataclasses.replace(state_out, config=config)

    with context(context.on.config_changed(), state_out) as manager:
        state_out = manager.run()

        assert state_out.get_relation(nginx_relation.id).local_app_data == {
            "service-namespace": manager.charm.model.name,
            "service-hostname": external_hostname,
            "service-name": manager.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": tls_secret_name,
            "backend-protocol": "HTTP",
        }


def test_ready(context, state, temporal_ui_container):
    # In the reconciler, pebble_ready converges fully
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

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
                "period": "10s",                "threshold": 3,                "threshold": 3,
            }
        },
    }

    assert state_out.get_container("temporal-ui").service_statuses["temporal-ui"] == ops.pebble.ServiceStatus.ACTIVE


def test_auth(
    context,
    nginx_state,
    temporal_ui_container,
    config_with_auth_enabled,
    external_hostname,
):
    # Set auth config upfront
    state = dataclasses.replace(nginx_state, config=config_with_auth_enabled)
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

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


def test_update_status_up(context, state, temporal_ui_container, temporal_ui_container_initialized):
    # First reconcile to establish the plan
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    # Replace with initialized container that has UP check
    state_out = dataclasses.replace(state_out, containers=[temporal_ui_container_initialized])

    state_out = context.run(context.on.update_status(), state_out)

    assert state_out.unit_status == ops.ActiveStatus()


def test_update_status_down(context, state, temporal_ui_container, temporal_ui_container_initialized):
    # First reconcile to establish the plan
    state_out = context.run(context.on.pebble_ready(temporal_ui_container), state)

    # Replace with container that has DOWN check
    temporal_container_down = dataclasses.replace(
        temporal_ui_container_initialized,
        check_infos=[
            ops.testing.CheckInfo(
                "up",
                status=ops.pebble.CheckStatus.DOWN,
            )
        ],
    )
    state_out = dataclasses.replace(state_out, containers=[temporal_container_down])

    state_out = context.run(context.on.update_status(), state_out)

    assert state_out.unit_status == ops.MaintenanceStatus("Status check: DOWN")
