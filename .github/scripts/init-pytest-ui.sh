#!/bin/bash
set -euo pipefail

echo "UI pre-run: ensuring pytest accepts charm-file argument"

# Create a temporary conftest.py that adds the --charm-file flag
cat > tests/conftest.py <<'EOF'
def pytest_addoption(parser):
    parser.addoption("--charm-file", action="store", default=None)
EOF

juju add-model testing || juju switch testing || true

if command -v k8s >/dev/null 2>&1; then
    echo "Enabling ingress in Canonical Kubernetes..."
    sudo k8s enable ingress || true

    echo "Configuring static load-balancer range (fixed, like MicroK8s)..."
    sudo k8s set load-balancer.cidrs=10.1.0.100-10.1.0.120 \
             load-balancer.enabled=true load-balancer.l2-mode=true
fi
