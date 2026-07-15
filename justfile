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

# Run integration tests (packs the charm into build/ first, as the jubilant refresh
# test expects a local *.charm. It must not live in the project root: pytest-operator's
# build_charm (used by the other integration tests) relocates *.charm files out of the
# root, which would remove it before the refresh test runs.)
integration *args:
	#!/usr/bin/bash
	set -euo pipefail
	rm -f ./*.charm build/*.charm
	mkdir -p build
	charmcraft pack
	mv ./*.charm build/
	uv tool run --with tox-uv tox -e integration -- {{args}}

# Print system state to help debug a failed run
get-system-state:
	#!/usr/bin/bash
	df -h
	echo "---"
	juju status --relations --storage --color || true
	echo "---"
	sudo k8s status || true
