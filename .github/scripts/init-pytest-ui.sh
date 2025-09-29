#!/bin/bash
set -euo pipefail

juju add-model testing || juju switch testing || true

if command -v k8s >/dev/null 2>&1; then
    echo "Enabling ingress in Canonical Kubernetes..."
    sudo k8s enable ingress || true
fi

echo ">>> Installing nginx ingress controller before Juju deploys anything..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml

# Wait for controller to be ready
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=5m

# Show service details (debugging)
kubectl -n ingress-nginx get svc ingress-nginx-controller -o wide
