#!/usr/bin/env bash
# Delete the local target cluster (recorded logs under data/logs/ are kept).
set -euo pipefail
# kind writes ~/.kube/config; some kubectl builds (e.g. k3s) default to another file
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
kind delete cluster --name "${KIND_CLUSTER_NAME:-hs-target}"
