[![Charmhub Badge](https://charmhub.io/temporal-ui-k8s/badge.svg)](https://charmhub.io/temporal-ui-k8s)
[![Release Edge](https://github.com/canonical/temporal-ui-k8s-operator/actions/workflows/test_and_publish_charm.yaml/badge.svg)](https://github.com/canonical/temporal-ui-k8s-operator/actions/workflows/test_and_publish_charm.yaml)

# Temporal UI K8s Operator

This is the Kubernetes Python Operator for the
[Temporal web UI tools](https://temporal.io/).

## Description

Temporal is a developer-first, open source platform that ensures the successful
execution of services and applications (using workflows).

Use Workflow as Code (TM) to build and operate resilient applications. Leverage
developer friendly primitives and avoid fighting your infrastructure

This operator provides the Temporal web UI, and consists of Python scripts which
wraps the versions distributed by
[temporalio](https://hub.docker.com/r/temporalio/ui).

## Usage

Please check the
[Temporal server operator](https://charmhub.io/temporal-k8s)
for usage instructions.

### Temporal endpoint resolution

`temporal-host-info` relation is the preferred source of Temporal server address. If relation data is unavailable, the deprecated `server-name` config option is used as a temporary fallback for upgrade compatibility.

`server-name` is deprecated and will be removed in a future release. Prefer integrating `temporal-host-info` relation instead of relying on config fallback.

## Contributing

This charm is still in active development. Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm
following best practice guidelines, and `CONTRIBUTING.md` for developer guidance.