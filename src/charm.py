#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm definition and helpers."""

import hashlib
import json
import logging
import os

from charms.certificate_transfer_interface.v1.certificate_transfer import (
    CertificateTransferRequires,
)
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.temporal_k8s.v0.temporal_host_info import TemporalHostInfoRequirer
from charms.traefik_k8s.v2.ingress import (
    IngressPerAppReadyEvent,
    IngressPerAppRequirer,
    IngressPerAppRevokedEvent,
)
from jinja2 import Environment, FileSystemLoader
from ops import main, pebble
from ops.charm import (
    CharmBase,
    ConfigChangedEvent,
    InstallEvent,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationEvent,
    RelationJoinedEvent,
    UpdateStatusEvent,
)
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import CheckStatus

from log import log_event_handler

REQUIRED_AUTH_PARAMETERS = ["auth-provider-url", "auth-client-id", "auth-client-secret", "auth-scopes"]
RECEIVE_CA_CERT_RELATION_NAME = "receive-ca-cert"
CA_CERT_PATH = "/home/ui-server/certs/ca.pem"
CONFIG_PATH = "/home/ui-server/config/charm.yaml"
WORKLOAD_VERSION = "2.27.1"

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class ExitWithStatusError(Exception):
    """Exception raised to exit the reconciliation with a specific unit status.

    Attrs:
        msg: The status message.
        status_type: The ops status class to report.
        status: The Juju unit status represented by the exception.
    """

    def __init__(self, msg: str, status_type):
        """Construct.

        Args:
            msg: The status message.
            status_type: The ops status class to report.
        """
        super().__init__(str(msg))
        self.msg = str(msg)
        self.status_type = status_type

    @property
    def status(self):
        """Return the Juju unit status represented by this exception."""
        return self.status_type(self.msg)


class ExitWithoutStatusChange(Exception):  # noqa: N818
    """Exception raised to exit the reconciliation leaving the unit status as it is.

    The status that was announced for the event, if any, is kept; otherwise the previous status
    stays.
    """


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

        # Actions keep a dedicated handler: they have a request/response contract.
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

        self.host_info = TemporalHostInfoRequirer(self)

        # The CA that issued the frontend certificate, used to verify the
        # frontend when it serves gRPC over TLS. It comes straight from the
        # certificates provider, not from the Temporal server charm.
        self.ca_transfer = CertificateTransferRequires(self, RECEIVE_CA_CERT_RELATION_NAME)

        # Every event the charm reacts to converges on the single reconciler.
        for event in [
            self.on.install,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.update_status,
            self.on.temporal_ui_pebble_ready,
            self.on.peer_relation_changed,
            self.on.ui_relation_joined,
            self.on.ui_relation_changed,
            self.on.ui_relation_broken,
            self.ingress.on.ready,
            self.ingress.on.revoked,
            self.host_info.on.temporal_host_info_changed,
            self.host_info.on.temporal_host_info_unavailable,
            self.ca_transfer.on.certificate_set_updated,
            self.ca_transfer.on.certificates_removed,
        ]:
            self.framework.observe(event, self._reconcile)

    @property
    def _ca_bundle(self) -> str:
        """Return the CA bundle received over certificate_transfer, or an empty string.

        The library returns an unordered set; sorting keeps the bundle, and so
        its hash in the Pebble environment, stable across hooks.
        """
        return "\n".join(sorted(cert.strip() for cert in self.ca_transfer.get_all_certificates()))

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
    def _reconcile(self, event):
        """Reconcile the charm towards the desired state; idempotent for every event.

        Whatever the event, a reconciliation that reaches the workload ends with the "replanning
        application" status; only update-status, which is the periodic health check of a workload
        whose plan is in place, reports the unit as active.

        Args:
            event: The event that triggered the reconciliation.
        """
        pending_status = self._pending_status(event)
        try:
            if isinstance(event, InstallEvent):
                raise ExitWithStatusError("installing temporal ui tools", MaintenanceStatus)

            self._mirror_server_status(event)
            if self._is_ui_relation_event(event) and not self._peer_relation_ready():
                raise ExitWithoutStatusChange()
            self._log_event(event)

            self._check_ready()

            container = self.unit.get_container(self.name)
            update_status = isinstance(event, UpdateStatusEvent)
            if not (update_status and self._validate_pebble_plan(container)):
                self._check_container_can_connect(container)
                context = self._build_context()
                self._configure_workload(container, context)
                self._apply_layer(container, context)
                raise ExitWithStatusError("replanning application", MaintenanceStatus)

            self._check_service_up(container)
        except ExitWithoutStatusChange:
            if pending_status is not None:
                self.unit.status = pending_status
            return
        except ExitWithStatusError as e:
            self.unit.status = e.status
            return

        self.unit.set_workload_version(WORKLOAD_VERSION)
        message = "auth enabled" if self.config["auth-enabled"] else ""
        self.unit.status = ActiveStatus(message)

    @log_event_handler(logger)
    def _on_restart(self, event):
        """Restart Temporal ui action handler.

        Args:
            event:The event triggered by the restart action
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.fail("Waiting for Pebble: the workload container is not ready")
            return

        self.unit.status = MaintenanceStatus("restarting ui")
        container.restart(self.name)

        event.set_results({"result": "worker successfully restarted"})

    def _pending_status(self, event):
        """Return the status the event announces while it is reconciled.

        It stays as the unit status if the reconciliation ends without a status of its own, e.g.
        because the workload container cannot be reached yet.

        Args:
            event: The event being reconciled.

        Returns:
            The announced status, or None if the event announces none.
        """
        if isinstance(event, ConfigChangedEvent):
            return WaitingStatus("configuring temporal")
        if not self._is_ui_relation_event(event) or not self._peer_relation_ready():
            return None
        if isinstance(event, RelationJoinedEvent):
            return WaitingStatus(f"handling {event.relation.name} change")
        if isinstance(event, RelationBrokenEvent):
            return WaitingStatus(f"handling {event.relation.name} removal")
        return None

    @staticmethod
    def _is_ui_relation_event(event) -> bool:
        """Report whether the event is a ui:temporal relation event.

        Args:
            event: The event being reconciled.

        Returns:
            True for a ui:temporal relation event.
        """
        return isinstance(event, RelationEvent) and event.relation.name == "ui"

    def _log_event(self, event):
        """Log what the event reports, as it was logged by the dedicated handlers.

        Args:
            event: The event being reconciled.
        """
        if isinstance(event, IngressPerAppReadyEvent):
            logger.info("Ingress is ready: %s", self.ingress.url)
        elif isinstance(event, IngressPerAppRevokedEvent):
            logger.info("Ingress revoked")
        elif isinstance(event, RelationChangedEvent) and self._is_ui_relation_event(event):
            logger.debug(f"ui:temporal: server is {self._server_status()}")

    def _peer_relation_ready(self) -> bool:
        """Report whether the peer relation exists."""
        return self.model.get_relation("peer") is not None

    def _server_status(self):
        """Return the status the Temporal server publishes on the ui:temporal relation, or None."""
        relation = self.model.get_relation("ui")
        if relation is None or relation.app is None:
            return None
        return relation.data[relation.app].get("server_status")

    def _mirror_server_status(self, event):
        """Keep the `server_status` value of the previous revisions up to date; leader only.

        This unit reads the status from the ui:temporal relation. During a rolling upgrade the
        units still running a previous revision read it from the peer relation, where it is JSON
        encoded, and the leader may already be upgraded and the only one updating it: the value is
        never removed or reshaped, and kept up to date in the format of the previous revisions.

        Args:
            event: The event being reconciled.
        """
        peer = self.model.get_relation("peer")
        if not self.unit.is_leader() or peer is None:
            return

        if isinstance(event, RelationBrokenEvent) and event.relation.name == "ui":
            status = "blocked"
        elif self.model.get_relation("ui") is not None:
            status = self._server_status()
        else:
            return

        mirrored = json.dumps(status)
        if peer.data[self.app].get("server_status", "null") != mirrored:
            peer.data[self.app]["server_status"] = mirrored

    def _check_ready(self):
        """Verify that the configuration and the relations are valid and ready.

        Raises:
            ExitWithStatusError: If they are not.
        """
        try:
            self._validate()
        except ValueError as err:
            raise ExitWithStatusError(str(err), BlockedStatus) from err

    def _check_container_can_connect(self, container):
        """Verify that the workload container is reachable.

        Args:
            container: application container

        Raises:
            ExitWithoutStatusChange: If it is not; pebble-ready or update-status reconciles it.
        """
        if not container.can_connect():
            raise ExitWithoutStatusChange()

    def _check_service_up(self, container):
        """Verify that the workload reports as up.

        Args:
            container: application container

        Raises:
            ExitWithStatusError: If the `up` check of the workload is not passing.
        """
        check = container.get_check("up")
        if check.status != CheckStatus.UP:
            raise ExitWithStatusError("Status check: DOWN", MaintenanceStatus)

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
        if not self._peer_relation_ready():
            raise ValueError("peer relation not ready")

        ui_relations = self.model.relations["ui"]
        if not ui_relations:
            raise ValueError("ui:temporal relation: not available")
        if not self._server_status() == "ready":
            raise ValueError("ui:temporal relation: server is not ready")
        if not (self.host_info.host and self.host_info.port):
            raise ValueError("temporal-host-info relation not established")
        self._validate_tls()
        if self.model.relations.get("ingress") and self.model.relations.get("nginx-route"):
            raise ValueError("Only one ingress solution is allowed - remove the ingress or the nginx-route relation")

        if self.config["auth-enabled"]:
            for param in REQUIRED_AUTH_PARAMETERS:
                if self.config[param].strip() == "":
                    raise ValueError(f"Invalid config: {param} value missing")

            if not self.model.relations.get("nginx-route") and not self.model.relations.get("ingress"):
                raise ValueError("Invalid config: auth cannot work without ingress relation")

    def _validate_tls(self):
        """Validate that the UI can verify the Temporal frontend when it serves TLS.

        Raises:
            ValueError: if the frontend serves TLS and no CA has been received.
        """
        if self.host_info.tls and not self._ca_bundle:
            raise ValueError(f"Temporal frontend uses TLS: integrate {RECEIVE_CA_CERT_RELATION_NAME} with its CA")

    def _build_context(self) -> dict:
        """Return the workload configuration: config file content and service environment.

        Returns:
            The workload configuration.
        """
        options = {
            "log-level": "LOG_LEVEL",
            "port": "TEMPORAL_UI_PORT",
            "default-namespace": "TEMPORAL_DEFAULT_NAMESPACE",
            "auth-enabled": "TEMPORAL_AUTH_ENABLED",
            "codec-endpoint": "TEMPORAL_CODEC_ENDPOINT",
            "codec-pass-access-token": "TEMPORAL_CODEC_PASS_ACCESS_TOKEN",
            "start-workflow-disabled": "TEMPORAL_START_WORKFLOW_DISABLED",
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

        context["TEMPORAL_ADDRESS"] = f"{self.host_info.host}:{self.host_info.port}"

        if self.host_info.tls:
            # ui-server reads the CA file only at startup, and the path never
            # changes, so the bundle's hash goes into the Pebble environment:
            # a rotated CA then changes the layer and restarts the service.
            context.update(
                {
                    "TEMPORAL_TLS_CA": CA_CERT_PATH,
                    "TEMPORAL_TLS_CA_HASH": hashlib.sha256(self._ca_bundle.encode()).hexdigest(),
                    # Without host verification ui-server skips certificate
                    # verification entirely, CA included.
                    "TEMPORAL_TLS_ENABLE_HOST_VERIFICATION": "true",
                }
            )

        return context

    def _configure_workload(self, container, context):
        """Write the workload files that differ from what the configuration calls for.

        Args:
            container: application container
            context: The workload configuration.
        """
        logger.info("configuring temporal ui")
        if self.host_info.tls:
            self._push_if_changed(container, CA_CERT_PATH, self._ca_bundle)
        self._push_if_changed(container, CONFIG_PATH, render("config.jinja", context))

    @staticmethod
    def _push_if_changed(container, path, content):
        """Push a file to the container unless it already has that content.

        Args:
            container: application container
            path: Absolute path of the file in the container.
            content: The wanted content of the file.
        """
        if container.exists(path) and container.pull(path).read() == content:
            return
        container.push(path, content, make_dirs=True)

    def _apply_layer(self, container, context):
        """Add the Pebble layer and replan when the plan changed or the service is not running.

        Args:
            container: application container
            context: The workload configuration, used as the service environment.
        """
        logger.info("planning temporal ui execution")
        pebble_layer = {
            "summary": "temporal server layer",
            "services": {
                self.name: {
                    "summary": "temporal ui",
                    "command": "ui-server --root /home/ui-server --env charm start",
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

        plan_before = container.get_plan().to_dict()
        container.add_layer(self.name, pebble_layer, combine=True)
        plan_changed = container.get_plan().to_dict() != plan_before

        service = container.get_services(self.name).get(self.name)
        if plan_changed or service is None or not service.is_running():
            container.replan()


if __name__ == "__main__":  # pragma: nocover
    main.main(TemporalUiK8SOperatorCharm)
