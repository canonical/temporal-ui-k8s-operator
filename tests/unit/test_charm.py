# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


"""Temporal admin charm unit tests."""

# pylint:disable=protected-access

import json
from unittest import TestCase

from ops.model import BlockedStatus
from ops.testing import Harness

from charm import TemporalUiK8SOperatorCharm
from state import State

APP_NAME = "temporal-ui"
UI_PORT = "8080"


class TestCharm(TestCase):
    """Unit tests for charm.

    Attrs:
        maxDiff: Specifies max difference shown by failed tests.
    """

    maxDiff = None

    def setUp(self):
        """Setup for the unit tests."""
        self.harness = Harness(TemporalUiK8SOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.set_can_connect(APP_NAME, True)
        self.harness.set_leader(True)
        self.harness.set_model_name("temporal-model")
        self.harness.begin()

    def test_initial_plan(self):
        """The initial pebble plan is empty."""
        initial_plan = self.harness.get_container_pebble_plan(APP_NAME).to_dict()
        self.assertEqual(initial_plan, {})

    def test_blocked_by_temporal_server(self):
        """The charm is blocked without a temporal:ui relation."""
        harness = self.harness

        # Simulate peer relation readiness.
        harness.add_relation("peer", "temporal")

        # Simulate pebble readiness.
        container = harness.model.unit.get_container(APP_NAME)
        harness.charm.on.temporal_ui_pebble_ready.emit(container)

        # The BlockStatus is set with a message.
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus("ui:temporal relation: not available"),
        )

    def test_blocked_by_peer_relation_not_ready(self):
        """The charm is blocked without a peer relation."""
        harness = self.harness

        # Simulate pebble readiness.
        container = harness.model.unit.get_container(APP_NAME)
        harness.charm.on.temporal_ui_pebble_ready.emit(container)

        # No plans are set yet.
        got_plan = harness.get_container_pebble_plan(APP_NAME).to_dict()
        self.assertEqual(got_plan, {})

        # The BlockStatus is set with a message.
        self.assertEqual(harness.model.unit.status, BlockedStatus("peer relation not ready"))

    def test_ingress(self):
        """The charm relates correctly to the nginx ingress charm and can be configured."""
        harness = self.harness

        # Simulate peer relation readiness.
        harness.add_relation("peer", "temporal")

        # Add the temporal relation.
        harness.add_relation("ui", "temporal")

        simulate_lifecycle(harness)

        nginx_route_relation_id = harness.add_relation("nginx-route", "ingress")
        harness.charm._require_nginx_route()

        assert harness.get_relation_data(nginx_route_relation_id, harness.charm.app) == {
            "service-namespace": harness.charm.model.name,
            "service-hostname": harness.charm.app.name,
            "service-name": harness.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": "temporal-tls",
            "backend-protocol": "HTTP",
        }

        new_hostname = "new-temporal-ui-k8s"
        harness.update_config({"external-hostname": new_hostname})
        harness.charm._require_nginx_route()

        assert harness.get_relation_data(nginx_route_relation_id, harness.charm.app) == {
            "service-namespace": harness.charm.model.name,
            "service-hostname": new_hostname,
            "service-name": harness.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": "temporal-tls",
            "backend-protocol": "HTTP",
        }

        new_tls = "new-tls"
        harness.update_config({"tls-secret-name": new_tls})
        harness.charm._require_nginx_route()

        assert harness.get_relation_data(nginx_route_relation_id, harness.charm.app) == {
            "service-namespace": harness.charm.model.name,
            "service-hostname": new_hostname,
            "service-name": harness.charm.app.name,
            "service-port": UI_PORT,
            "tls-secret-name": new_tls,
            "backend-protocol": "HTTP",
        }

    def test_ready(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness

        # Simulate peer relation readiness.
        harness.add_relation("peer", "temporal")

        # Add the temporal relation.
        harness.add_relation("ui", "temporal")

        simulate_lifecycle(harness)

        # The plan is generated after pebble is ready.
        want_plan = {
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
                    },
                }
            },
        }

        got_plan = harness.get_container_pebble_plan(APP_NAME).to_dict()
        self.assertEqual(got_plan, want_plan)

        # The service was started.
        service = harness.model.unit.get_container(APP_NAME).get_service(APP_NAME)
        self.assertTrue(service.is_running())

    def test_auth(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness

        # Simulate peer relation readiness.
        harness.add_relation("peer", "temporal")

        # Add the temporal relation.
        harness.add_relation("ui", "temporal")

        simulate_lifecycle(harness)
        harness.add_relation("nginx-route", "ingress")

        harness.update_config(
            {
                "auth-enabled": True,
                "auth-provider-url": "some-provider-url",
                "auth-client-id": "some-client-id",
                "auth-client-secret": "some-client-secret",
            }
        )

        # The plan is generated after pebble is ready.
        want_plan = {
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
                        "TEMPORAL_AUTH_CALLBACK_URL": f"https://{harness.model.config['external-hostname']}/auth/sso/callback",
                    },
                }
            },
        }

        got_plan = harness.get_container_pebble_plan(APP_NAME).to_dict()
        self.assertEqual(got_plan, want_plan)

        # The service was started.
        service = harness.model.unit.get_container(APP_NAME).get_service(APP_NAME)
        self.assertTrue(service.is_running())


def simulate_lifecycle(harness):
    """Simulate a healthy charm life-cycle.

    Args:
        harness: ops.testing.Harness object used to simulate charm lifecycle.
    """
    # Simulate pebble readiness.
    container = harness.model.unit.get_container(APP_NAME)
    harness.charm.on.temporal_ui_pebble_ready.emit(container)

    # Simulate server readiness.
    app = type("App", (), {"name": "temporal-ui-k8s"})()
    relation = type(
        "Relation",
        (),
        {"data": {app: {"server_status": "ready"}}, "name": "ui", "id": 42},
    )()
    unit = type("Unit", (), {"app": app, "name": "temporal-ui-k8s/0"})()
    event = type("Event", (), {"app": app, "relation": relation, "unit": unit})()
    harness.charm._on_ui_relation_changed(event)


def make_ui_changed_event(rel_name):
    """Create and return a mock relation changed event.

    The event is generated by the relation with the given name.

    Args:
        rel_name: Relationship name.

    Returns:
        Event dict.
    """
    return type(
        "Event",
        (),
        {
            "data": {
                "server_status": "ready",
            },
            "relation": type("Relation", (), {"name": rel_name}),
        },
    )


class TestState(TestCase):
    """Unit tests for state.

    Attrs:
        maxDiff: Specifies max difference shown by failed tests.
    """

    maxDiff = None

    def test_get(self):
        """It is possible to retrieve attributes from the state."""
        state = make_state({"foo": json.dumps("bar")})
        self.assertEqual(state.foo, "bar")
        self.assertIsNone(state.bad)

    def test_set(self):
        """It is possible to set attributes in the state."""
        data = {"foo": json.dumps("bar")}
        state = make_state(data)
        state.foo = 42
        state.list = [1, 2, 3]
        self.assertEqual(state.foo, 42)
        self.assertEqual(state.list, [1, 2, 3])
        self.assertEqual(data, {"foo": "42", "list": "[1, 2, 3]"})

    def test_del(self):
        """It is possible to unset attributes in the state."""
        data = {"foo": json.dumps("bar"), "answer": json.dumps(42)}
        state = make_state(data)
        del state.foo
        self.assertIsNone(state.foo)
        self.assertEqual(data, {"answer": "42"})
        # Deleting a name that is not set does not error.
        del state.foo

    def test_is_ready(self):
        """The state is not ready when it is not possible to get relations."""
        state = make_state({})
        self.assertTrue(state.is_ready())

        state = State("myapp", lambda: None)
        self.assertFalse(state.is_ready())


def make_state(data):
    """Create state object.

    Args:
        data: Data to be included in state.

    Returns:
        State object with data.
    """
    app = "myapp"
    rel = type("Rel", (), {"data": {app: data}})()
    return State(app, lambda: rel)
