#!/bin/bash
set -euo pipefail

juju add-model testing || juju switch testing || true

if command -v k8s >/dev/null 2>&1; then
    echo "Enabling ingress in Canonical Kubernetes..."
    sudo k8s enable ingress || true

    echo "Configuring static load-balancer CIDR (same as microk8s setup)..."
    sudo k8s set load-balancer.cidrs=10.15.119.2-10.15.119.4 \
                 load-balancer.enabled=true load-balancer.l2-mode=true
fi

echo ">>> Installing nginx ingress controller before Juju deploys anything..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml

# Wait for controller to be ready
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=5m

# Show service details (debugging)
kubectl -n ingress-nginx get svc ingress-nginx-controller -o wide
