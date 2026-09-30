# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

# flake8: noqa

"""Temporal UI charm integration test helpers."""

import contextlib
import logging
import socket
from collections.abc import Generator

import jubilant
from jubilant.statustypes import UnitStatus

logger = logging.getLogger(__name__)


def unit_status(juju: jubilant.Juju, app: str, num: int = 0) -> UnitStatus:
    """Return the status of a single unit of an application.

    Args:
        juju: Jubilant Juju client bound to the test model.
        app: Application the unit belongs to.
        num: Unit number within the application.
    Returns:
        Status of the requested unit.
    """
    return juju.status().apps[app].units[f"{app}/{num}"]


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


@contextlib.contextmanager
def fast_forward(juju: jubilant.Juju) -> Generator[None, None, None]:
    """Temporarily speed up update-status hooks to fire every 10s.

    Args:
        juju: Jubilant Juju client bound to the test model.
    """
    old = juju.model_config()["update-status-hook-interval"]
    juju.model_config({"update-status-hook-interval": "10s"})
    try:
        yield
    finally:
        juju.model_config({"update-status-hook-interval": old})


def scale(juju: jubilant.Juju, app: str, units: int):
    """Scale the application to the provided number and wait for idle.

    Args:
        juju: Jubilant Juju client bound to the test model.
        app: Application to be scaled.
        units: Number of units required.
    """
    juju.cli("scale-application", app, str(units))

    with fast_forward(juju):
        juju.wait(
            lambda status: (jubilant.all_active(status, app) and len(status.apps[app].units) == units),
            timeout=600,
            successes=30,
        )
        assert len(juju.status().apps[app].units) == units
