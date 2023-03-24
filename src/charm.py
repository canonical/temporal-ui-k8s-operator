#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm definition and helpers."""

import logging
import os

from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from jinja2 import Environment, FileSystemLoader
from ops import framework, main
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

from log import log_event_handler

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
    return Environment(loader=loader).get_template(template_name).render(**context)


class TemporalUiK8SOperatorCharm(CharmBase):
    """Temporal UI charm.

    Attrs:
        _state: used to store data that is persisted across invocations.
        external_hostname: DNS listing used for external connections.
    """

    _state = framework.StoredState()

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

        # Handle basic charm lifecycle.
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.temporal_ui_pebble_ready, self._on_temporal_ui_pebble_ready)

        # Handle ui:temporal relation.
        self._state.set_default(server_status="blocked")
        self.framework.observe(self.on.ui_relation_joined, self._on_ui_relation_joined)
        self.framework.observe(self.on.ui_relation_changed, self._on_ui_relation_changed)
        self.framework.observe(self.on.ui_relation_broken, self._on_ui_relation_broken)

        # Handle Ingress.
        self.ingress = IngressRequires(
            self,
            {
                "service-hostname": self.external_hostname,
                "service-name": self.app.name,
                "service-port": 8080,
            },
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
    def _on_config_changed(self, event):
        """Handle configuration changes.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = WaitingStatus("configuring temporal")
        self.ingress.update_config({"service-hostname": self.external_hostname})
        self._update(event)

    @log_event_handler(logger)
    def _on_ui_relation_joined(self, event):
        """Handle joining a ui:temporal relation.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = WaitingStatus(f"handling {event.relation.name} change")
        self._state.server_status = event.relation.data[event.app].get("server_status")
        self._update(event)

    @log_event_handler(logger)
    def _on_ui_relation_changed(self, event):
        """Handle changes on the ui:temporal relation.

        Args:
            event: The event triggered when the relation changed.
        """
        self._state.server_status = event.relation.data[event.app].get("server_status")
        logger.debug(f"ui:temporal: server is {self._state.server_status}")
        self._update(event)

    @log_event_handler(logger)
    def _on_ui_relation_broken(self, event):
        """Handle removal of the ui:temporal relation.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = WaitingStatus(f"handling {event.relation.name} removal")
        self._state.server_status = "blocked"
        self._update(event)

    def _validate(self):
        """Validate that configuration and relations are valid and ready.

        Raises:
            ValueError: in case of invalid configuration.
        """
        ui_relations = self.model.relations["ui"]
        if not ui_relations:
            raise ValueError("ui:temporal relation: not available")

        if not self._state.server_status == "ready":
            raise ValueError("ui:temporal relation: server is not ready")

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
        }
        context = {config_key: self.config[key] for key, config_key in options.items()}

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
                }
            },
        }

        container.add_layer(self.name, pebble_layer, combine=True)
        container.replan()

        self.unit.status = ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    main.main(TemporalUiK8SOperatorCharm)