# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

set export

[private]
default:
	just --list

# Format the code
format:
	uv tool run --with tox-uv tox -e fmt

# Run linters
lint:
	uv tool run --with tox-uv tox -e lint

# Run unit tests
unit:
	uv tool run --with tox-uv tox -e unit

# Run static analysis checks
static:
	uv tool run --with tox-uv tox -e static

# Run integration tests (packs the charm first, as the jubilant tests expect a local *.charm)
integration *args:
	charmcraft pack
	uv tool run --with tox-uv tox -e integration -- {{args}}

# Print system state to help debug a failed run
get-system-state:
	#!/usr/bin/bash
	df -h
	echo "---"
	juju status --relations --storage --color || true
	echo "---"
	sudo k8s status || true
