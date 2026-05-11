#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm definition and helpers."""

import logging
import os

import ops
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.temporal_k8s.v0.temporal_host_info import TemporalHostInfoRequirer
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from jinja2 import Environment, FileSystemLoader
from ops import main, pebble
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import CheckStatus

from log import log_event_handler
from state import State

REQUIRED_AUTH_PARAMETERS = ["auth-provider-url", "auth-client-id", "auth-client-secret", "auth-scopes"]
WORKLOAD_VERSION = "2.27.1"

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

        # Route all reconcilable events to _reconcile
        reconcile_events = [
            self.on.install,
            self.on.start,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.update_status,
            self.on.leader_elected,
            self.on["temporal-ui"].pebble_ready,
            self.on["peer"].relation_changed,
            self.on["ui"].relation_created,
            self.on["ui"].relation_joined,
            self.on["ui"].relation_changed,
            self.on["ui"].relation_departed,
            self.on["ui"].relation_broken,
        ]
        for event in reconcile_events:
            self.framework.observe(event, self._reconcile)

        # Dedicated handlers
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.restart_action, self._on_restart)

        # Handle Nginx Ingress.
        self._require_nginx_route()

        # Handle Traefik Ingress.
        self.ingress = IngressPerAppRequirer(
            self,
            relation_name="ingress",
            port=self.config["port"],
            strip_prefix=True,
        )
        self.framework.observe(self.ingress.on.ready, self._reconcile)
        self.framework.observe(self.ingress.on.revoked, self._reconcile)

        self.host_info = TemporalHostInfoRequirer(self)
        self.framework.observe(self.host_info.on.temporal_host_info_changed, self._reconcile)
        self.framework.observe(self.host_info.on.temporal_host_info_unavailable, self._reconcile)

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

    # -- Central Reconciliation Loop -----------------------------------

    @log_event_handler(logger)
    def _reconcile(self, event):
        """Central reconciliation loop: read -> compute -> write.

        Args:
            event: The event that triggered reconciliation.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            return

        if not self._state.is_ready():
            return

        # Phase 1: Read inputs (safe to poll -- all from relation databags)
        if self.unit.is_leader():
            self._read_ui_relation_data(event)

        try:
            self._validate()
        except ValueError:
            return

        # Phase 2: Compute new state
        context = self._build_workload_context()
        if context is None:
            return

        config_content = render("config.jinja", context)

        pebble_layer = {
            "summary": "temporal server layer",
            "services": {
                self.name: {
                    "summary": "temporal ui",
                    "command": "ui-server --root /home/ui-server --env charm start",
                    "startup": "enabled",
                    "override": "replace",
                    "environment": context,
                    "on-check-failure": {"up": "ignore"},
                }
            },
            "checks": {
                "up": {
                    "override": "replace",
                    "period": "10s",
                    "threshold": 3,
                    "http": {"url": f"http://localhost:{self.config['port']}/"},
                }
            },
        }

        # Phase 3: Write outputs (only if changed)
        container.push("/home/ui-server/config/charm.yaml", config_content, make_dirs=True)

        current_plan = container.get_plan().to_dict()
        if current_plan.get("services") != pebble_layer.get("services") or current_plan.get(
            "checks"
        ) != pebble_layer.get("checks"):
            container.add_layer(self.name, pebble_layer, combine=True)
            container.replan()

        self.unit.set_workload_version(WORKLOAD_VERSION)

    def _read_ui_relation_data(self, event):
        """Read server_status from ui relation and persist to peer state.

        Safe to poll -- reads directly from relation databag.

        Args:
            event: The event that triggered the read.
        """
        if isinstance(event, ops.RelationBrokenEvent) and event.relation.name == "ui":
            self._state.server_status = "blocked"
            return

        ui_relations = self.model.relations["ui"]
        if not ui_relations:
            return

        for relation in ui_relations:
            server_status = relation.data.get(relation.app, {}).get("server_status")
            if server_status:
                self._state.server_status = server_status
                return

    def _build_workload_context(self):
        """Build the environment context for the Temporal UI workload.

        Returns:
            dict of environment variables, or None if host_info is not available.
        """
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

        if not (self.host_info.host and self.host_info.port):
            return None

        context["TEMPORAL_ADDRESS"] = f"{self.host_info.host}:{self.host_info.port}"
        return context

    # -- Status Reporting ----------------------------------------------

    def _on_collect_unit_status(self, event):
        """Report unit status based on current state.

        Args:
            event: The collect-unit-status event.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.add_status(WaitingStatus("Waiting for container"))
            return

        if not self._state.is_ready():
            event.add_status(BlockedStatus("peer relation not ready"))
            return

        try:
            self._validate()
        except ValueError as err:
            event.add_status(BlockedStatus(str(err)))
            return

        valid_pebble_plan = self._validate_pebble_plan(container)
        if not valid_pebble_plan:
            event.add_status(MaintenanceStatus("replanning application"))
            return

        check = container.get_check("up")
        if check.status != CheckStatus.UP:
            event.add_status(MaintenanceStatus("Status check: DOWN"))
            return

        message = "auth enabled" if self.config["auth-enabled"] else ""
        event.add_status(ActiveStatus(message))

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
        if not (self.host_info.host and self.host_info.port):
            raise ValueError("temporal-host-info relation not established")
        if self.model.relations.get("ingress") and self.model.relations.get("nginx-route"):
            raise ValueError("Only one ingress solution is allowed - remove the ingress or the nginx-route relation")

        if self.config["auth-enabled"]:
            for param in REQUIRED_AUTH_PARAMETERS:
                if self.config[param].strip() == "":
                    raise ValueError(f"Invalid config: {param} value missing")

            if not self.model.relations.get("nginx-route") and not self.model.relations.get("ingress"):
                raise ValueError("Invalid config: auth cannot work without ingress relation")

    # -- Dedicated Handlers --------------------------------------------

    @log_event_handler(logger)
    def _on_restart(self, event):
        """Restart Temporal ui action handler.

        Args:
            event:The event triggered by the restart action
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.fail("cannot connect to container")
            return

        self.unit.status = MaintenanceStatus("restarting ui")
        container.restart(self.name)

        event.set_results({"result": "worker successfully restarted"})


if __name__ == "__main__":  # pragma: nocover
    main.main(TemporalUiK8SOperatorCharm)
