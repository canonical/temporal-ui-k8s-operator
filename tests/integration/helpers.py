# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

# flake8: noqa

"""Temporal UI charm integration test helpers."""

import logging
import socket

import juju.errors
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


def _temporal_host_info_endpoint_missing(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "temporal-host-info" in msg and "has no" in msg


async def integrate_temporal_host_info_or_set_server_name(
    ops_test: OpsTest,
    *,
    ui_app: str,
    temporal_server_app: str,
) -> bool:
    """Integrate temporal-host-info if temporal-k8s exposes it; else set deprecated server-name.

    Published ``temporal-k8s`` revisions may not include the endpoint yet; tests still need UI active.

    Returns:
        True if the relation was integrated, False if server-name fallback was used.
    """
    try:
        await ops_test.model.integrate(
            f"{ui_app}:temporal-host-info",
            f"{temporal_server_app}:temporal-host-info",
        )
    except juju.errors.JujuAPIError as exc:
        if not _temporal_host_info_endpoint_missing(exc):
            raise
        logger.warning(
            "%s has no temporal-host-info; setting UI server-name=%s",
            temporal_server_app,
            temporal_server_app,
        )
        await ops_test.model.applications[ui_app].set_config({"server-name": temporal_server_app})
        return False
    return True


def gen_patch_getaddrinfo(host: str, resolve_to: str):  # noqa
    """Generate patched getaddrinfo function.

    This function is used to generate a patched getaddrinfo function that will resolve to the
    resolve_to address without having to actually register a host.

    Args:
        host: intended hostname of a given application.
        resolve_to: destination address for host to resolve to.
    Returns:
        A patching function for getaddrinfo.
    """
    original_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(*args):
        """Patch getaddrinfo to point to desired ip address.

        Args:
            args: original arguments to getaddrinfo when creating network connection.
        Returns:
            Patched getaddrinfo function.
        """
        if args[0] == host:
            return original_getaddrinfo(resolve_to, *args[1:])
        return original_getaddrinfo(*args)

    return patched_getaddrinfo


async def scale(ops_test: OpsTest, app, units):
    """Scale the application to the provided number and wait for idle.

    Args:
        ops_test: PyTest object.
        app: Application to be scaled.
        units: Number of units required.
    """
    await ops_test.model.applications[app].scale(scale=units)

    # Wait for model to settle
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[app],
            status="active",
            idle_period=30,
            raise_on_error=False,
            raise_on_blocked=True,
            timeout=600,
            wait_for_exact_units=units,
        )

        assert len(ops_test.model.applications[app].units) == units
