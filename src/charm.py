#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm definition and helpers."""

import logging
import os

from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from jinja2 import Environment, FileSystemLoader
from ops import main, pebble
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import CheckStatus

from log import log_event_handler
from state import State

REQUIRED_AUTH_PARAMETERS = ["auth-provider-url", "auth-client-id", "auth-client-secret", "auth-scopes"]
WORKLOAD_VERSION = "2.27.6"

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


def render(template_name, context):
    """Render the template with the given name using the given context dict.

    Args:
        template_name: File name to read the template from.
        context: Dict used for rendering.

    Returns:
        A dict containing the rendered template.
    """
    charm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    loader = FileSystemLoader(os.path.join(charm_dir, "templates"))
    return Environment(loader=loader, autoescape=True).get_template(template_name).render(**context)


class TemporalUiK8SOperatorCharm(CharmBase):
    """Temporal UI charm.

    Attrs:
        _state: used to store data that is persisted across invocations.
        external_hostname: DNS listing used for external connections.
    """

    @property
    def external_hostname(self):
        """Return the DNS listing used for external connections."""
        return self.config["external-hostname"] or self.app.name

    def __init__(self, *args):
        """Construct.

        Args:
            args: Ignore.
        """
        super().__init__(*args)
        self.name = "temporal-ui"
        self._state = State(self.app, lambda: self.model.get_relation("peer"))

        # Handle basic charm lifecycle.
        self.framework.observe(self.on.peer_relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.temporal_ui_pebble_ready, self._on_temporal_ui_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

        # Handle ui:temporal relation.
        self.framework.observe(self.on.ui_relation_joined, self._on_ui_relation_joined)
        self.framework.observe(self.on.ui_relation_changed, self._on_ui_relation_changed)
        self.framework.observe(self.on.ui_relation_broken, self._on_ui_relation_broken)

        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(self.on.update_status, self._on_update_status)

        # Handle Ingress.
        self._require_nginx_route()

    def _require_nginx_route(self):
        """Require nginx-route relation based on current configuration."""
        require_nginx_route(
            charm=self,
            service_hostname=self.external_hostname,
            service_name=self.app.name,
            service_port=self.config["port"],
            tls_secret_name=self.config["tls-secret-name"],
            backend_protocol="HTTP",
        )

    @log_event_handler(logger)
    def _on_install(self, event):
        """Install temporal UI tools.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = MaintenanceStatus("installing temporal ui tools")

    @log_event_handler(logger)
    def _on_temporal_ui_pebble_ready(self, event):
        """Define and start temporal UI using the Pebble API.

        Args:
            event: The event triggered when the relation changed.
        """
        self._update(event)

    @log_event_handler(logger)
    def _on_peer_relation_changed(self, event):
        """Handle peer relation changed event.

        Args:
            event: The event triggered when the relation changed.
        """
        self._update(event)

    @log_event_handler(logger)
    def _on_config_changed(self, event):
        """Handle configuration changes.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = WaitingStatus("configuring temporal")
        self._update(event)

    @log_event_handler(logger)
    def _on_restart(self, event):
        """Restart Temporal ui action handler.

        Args:
            event:The event triggered by the restart action
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        self.unit.status = MaintenanceStatus("restarting ui")
        container.restart(self.name)

        event.set_results({"result": "worker successfully restarted"})

    @log_event_handler(logger)
    def _on_update_status(self, event):
        """Handle `update-status` events.

        Args:
            event: The `update-status` event triggered at intervals.
        """
        try:
            self._validate()
        except ValueError:
            return

        container = self.unit.get_container(self.name)
        valid_pebble_plan = self._validate_pebble_plan(container)
        if not valid_pebble_plan:
            self._update(event)
            return

        check = container.get_check("up")
        if check.status != CheckStatus.UP:
            self.unit.status = MaintenanceStatus("Status check: DOWN")
            return

        self.unit.set_workload_version(WORKLOAD_VERSION)
        message = "auth enabled" if self.config["auth-enabled"] else ""
        self.unit.status = ActiveStatus(message)

    def _validate_pebble_plan(self, container):
        """Validate Temporal UI pebble plan.

        Args:
            container: application container

        Returns:
            bool of pebble plan validity
        """
        try:
            plan = container.get_plan().to_dict()
            return bool(plan["services"][self.name]["on-check-failure"])
        except (KeyError, pebble.ConnectionError):
            return False

    @log_event_handler(logger)
    def _on_ui_relation_joined(self, event):
        """Handle joining a ui:temporal relation.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self._state.is_ready():
            event.defer()
            return

        self.unit.status = WaitingStatus(f"handling {event.relation.name} change")
        if self.unit.is_leader():
            self._state.server_status = event.relation.data[event.app].get("server_status")

        self._update(event)

    @log_event_handler(logger)
    def _on_ui_relation_changed(self, event):
        """Handle changes on the ui:temporal relation.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self._state.is_ready():
            event.defer()
            return

        if self.unit.is_leader():
            self._state.server_status = event.relation.data[event.app].get("server_status")

        logger.debug(f"ui:temporal: server is {self._state.server_status}")
        self._update(event)

    @log_event_handler(logger)
    def _on_ui_relation_broken(self, event):
        """Handle removal of the ui:temporal relation.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self._state.is_ready():
            event.defer()
            return

        self.unit.status = WaitingStatus(f"handling {event.relation.name} removal")
        if self.unit.is_leader():
            self._state.server_status = "blocked"

        self._update(event)

    def _validate(self):
        """Validate that configuration and relations are valid and ready.

        Raises:
            ValueError: in case of invalid configuration.
        """
        if not self._state.is_ready():
            raise ValueError("peer relation not ready")

        ui_relations = self.model.relations["ui"]
        if not ui_relations:
            raise ValueError("ui:temporal relation: not available")
        if not self._state.server_status == "ready":
            raise ValueError("ui:temporal relation: server is not ready")

        if self.config["auth-enabled"]:
            for param in REQUIRED_AUTH_PARAMETERS:
                if self.config[param].strip() == "":
                    raise ValueError(f"Invalid config: {param} value missing")

            if not self.model.relations.get("nginx-route"):
                raise ValueError("Invalid config: auth cannot work without ingress relation")

    @log_event_handler(logger)
    def _update(self, event):
        """Update the Temporal UI configuration and replan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        try:
            self._validate()
        except ValueError as err:
            self.unit.status = BlockedStatus(str(err))
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        logger.info("configuring temporal ui")
        options = {
            "log-level": "LOG_LEVEL",
            "port": "TEMPORAL_UI_PORT",
            "default-namespace": "TEMPORAL_DEFAULT_NAMESPACE",
            "auth-enabled": "TEMPORAL_AUTH_ENABLED",
            "codec-endpoint": "TEMPORAL_CODEC_ENDPOINT",
            "codec-pass-access-token": "TEMPORAL_CODEC_PASS_ACCESS_TOKEN",
            "workflow-terminate-disabled": "TEMPORAL_WORKFLOW_TERMINATE_DISABLED",
            "workflow-cancel-disabled": "TEMPORAL_WORKFLOW_CANCEL_DISABLED",
            "workflow-signal-disabled": "TEMPORAL_WORKFLOW_SIGNAL_DISABLED",
            "workflow-reset-disabled": "TEMPORAL_WORKFLOW_RESET_DISABLED",
            "batch-actions-disabled": "TEMPORAL_BATCH_ACTIONS_DISABLED",
            "hide-workflow-query-errors": "TEMPORAL_HIDE_WORKFLOW_QUERY_ERRORS",
        }

        context = {config_key: self.config[key] for key, config_key in options.items()}
        if self.config["auth-enabled"]:
            auth_options = {
                "auth-provider-url": "TEMPORAL_AUTH_PROVIDER_URL",
                "auth-client-id": "TEMPORAL_AUTH_CLIENT_ID",
                "auth-client-secret": "TEMPORAL_AUTH_CLIENT_SECRET",
                "auth-scopes": "TEMPORAL_AUTH_SCOPES",
            }
            context.update({config_key: self.config[key] for key, config_key in auth_options.items()})
            context.update(
                {"TEMPORAL_AUTH_CALLBACK_URL": f"https://{self.config['external-hostname']}/auth/sso/callback"}
            )

        http_proxy = os.environ.get("JUJU_CHARM_HTTP_PROXY")
        https_proxy = os.environ.get("JUJU_CHARM_HTTPS_PROXY")
        no_proxy = os.environ.get("JUJU_CHARM_NO_PROXY")

        if http_proxy or https_proxy:
            context.update(
                {
                    "HTTP_PROXY": http_proxy,
                    "HTTPS_PROXY": https_proxy,
                    "NO_PROXY": no_proxy,
                }
            )

        config = render("config.jinja", context)
        container.push("/home/ui-server/config/charm.yaml", config, make_dirs=True)

        logger.info("planning temporal ui execution")
        pebble_layer = {
            "summary": "temporal server layer",
            "services": {
                self.name: {
                    "summary": "temporal ui",
                    "command": "./ui-server --env charm start",
                    "startup": "enabled",
                    "override": "replace",
                    # Including config values here so that a change in the
                    # config forces replanning to restart the service.
                    "environment": context,
                    "on-check-failure": {"up": "ignore"},
                }
            },
            "checks": {
                "up": {
                    "override": "replace",
                    "period": "10s",
                    "http": {"url": f"http://localhost:{self.config['port']}/"},
                }
            },
        }

        container.add_layer(self.name, pebble_layer, combine=True)
        container.replan()

        self.unit.status = MaintenanceStatus("replanning application")


if __name__ == "__main__":  # pragma: nocover
    main.main(TemporalUiK8SOperatorCharm)
