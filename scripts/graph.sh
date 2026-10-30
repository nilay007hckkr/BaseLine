#!/usr/bin/env bash
# Start or stop the local Neo4j graph layer (deploy/neo4j/compose.yaml).
# When NEO4J_PASSWORD is not set, a random password is generated once into data/neo4j/password
# (git-ignored, readable only by you); the backend reads the same file.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f deploy/neo4j/compose.yaml)
secret=data/neo4j/password

case "${1:-}" in
  up)
    if [ -z "${NEO4J_PASSWORD:-}" ]; then
      if [ ! -s "$secret" ]; then
        mkdir -p "$(dirname "$secret")"
        (umask 077 && head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$secret")
        echo "generated a local Neo4j password in $secret"
      fi
      NEO4J_PASSWORD="$(cat "$secret")"
      export NEO4J_PASSWORD
    fi
    "${compose[@]}" up -d --wait
    echo "Neo4j is up: browser http://localhost:7474 (user neo4j), bolt://localhost:7687"
    echo "next: make graph-sync"
    ;;
  down)
    NEO4J_PASSWORD="${NEO4J_PASSWORD:-unused}" "${compose[@]}" down
    echo "Neo4j stopped (data kept in the hindsight-graph_neo4j-data volume)"
    ;;
  *)
    echo "usage: scripts/graph.sh up|down" >&2
    exit 2
    ;;
esac
