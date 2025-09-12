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

    echo "Deriving LB CIDR after enabling ingress..."
    IPADDR=$(ip -4 -j route get 2.2.2.2 | jq -r '.[] | .prefsrc')
    LB_FIRST_ADDR="$(echo "${IPADDR}" | awk -F'.' '{print $1,$2,$3,100}' OFS='.')"
    LB_LAST_ADDR="$(echo "${IPADDR}" | awk -F'.' '{print $1,$2,$3,255}' OFS='.')"
    LB_ADDR_RANGE="${LB_FIRST_ADDR}-${LB_LAST_ADDR}"
    sudo k8s set load-balancer.cidrs=$LB_ADDR_RANGE load-balancer.enabled=true load-balancer.l2-mode=true
fi

