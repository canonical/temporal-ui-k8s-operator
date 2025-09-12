#!/bin/bash
set -euo pipefail

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

echo ">>> Installing nginx ingress controller before Juju deploys anything..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml

# Wait for controller to be ready
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=5m

# Show service details (debugging)
kubectl -n ingress-nginx get svc ingress-nginx-controller -o wide
