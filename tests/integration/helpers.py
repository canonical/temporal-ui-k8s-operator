#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd Ltd.
# See LICENSE file for licensing details.

# flake8: noqa

"""Temporal UI charm integration test helpers."""

import socket


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
