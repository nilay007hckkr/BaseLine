#!/usr/bin/env bash
# Online Boutique on a local kind cluster, using the release manifests from the cloned repository
# (pinned upstream images, nothing is built). The shop is served on http://localhost:8081 and the
# bundled load generator keeps traffic flowing.
set -euo pipefail
# kind writes ~/.kube/config; some kubectl builds (e.g. k3s) default to another file
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="${KIND_CLUSTER_NAME:-hs-target}"
CTX="kind-$CLUSTER"
MANIFESTS="$ROOT/data/repos/GoogleCloudPlatform__microservices-demo/release/kubernetes-manifests.yaml"

[[ -f "$MANIFESTS" ]] || { echo "Online Boutique is not cloned yet: run make ingest first"; exit 1; }
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "cluster $CLUSTER exists"
else
  kind create cluster --name "$CLUSTER" --config "$ROOT/target/kind.yaml" --wait 180s
fi
kubectl --context "$CTX" apply -f "$MANIFESTS"
kubectl --context "$CTX" patch service frontend-external --type merge \
  -p '{"spec":{"type":"NodePort","ports":[{"name":"http","port":80,"targetPort":8080,"nodePort":30081}]}}'
echo "waiting for every deployment (first start pulls the images; this can take a while)…"
kubectl --context "$CTX" wait --for=condition=available deployment --all --timeout=3600s
kubectl --context "$CTX" get pods
echo "shop: http://localhost:8081"
