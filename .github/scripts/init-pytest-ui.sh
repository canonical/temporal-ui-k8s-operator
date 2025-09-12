#!/bin/bash
set -euo pipefail

echo "UI pre-run: ensuring pytest accepts charm-file argument"

# Create a temporary conftest.py that adds the --charm-file flag
cat > tests/conftest.py <<'EOF'
def pytest_addoption(parser):
    parser.addoption("--charm-file", action="store", default=None)
EOF

juju add-model testing || juju switch testing || true
juju deploy nginx-ingress-integrator --channel edge --trust

