# ADR 0001: Neo4j as the graph layer, SQLite as the store ingest writes to

- Status: accepted (2026-10-31)
- Deciders: maintainer

## Context

Phase 1 kept the whole graph in SQLite (`nodes`, `edges` with provenance and evidence) and walked
it with SQL and NetworkX. The maintainer asked for a graph database. About 90 places in the code
read or write the graph tables. Ingest depends on SQLite transactions, the as-of tables (`commits`,
`changes`) that keep evaluation free of leakage, and FTS5 for keyword search. Unit tests are
hermetic and must not need a running server.

## Options

1. **Full move:** Neo4j becomes the only graph store; ingest, uploads and every query are
   rewritten; unit tests need Neo4j. Most change, highest risk.
2. **Graph layer:** SQLite stays the system of record; the graph is copied into Neo4j; graph reads
   go through one small interface with a Cypher and a SQL implementation.
3. **Export only:** keep SQLite and export the graph for viewing.

## Decision

Option 2, chosen by the maintainer.

- `hindsight.core.graphsync.sync()` copies all nodes and edges (with provenance and evidence) into
  Neo4j, one namespace per repository, idempotently, and removes what SQLite no longer has.
- `hindsight.core.graphstore.GraphReader` has six reads (neighbourhood edges, incoming edges,
  recent human commits, one hop along given edge types, edge pairs, doc mentions), implemented
  in Cypher (`Neo4jGraph`) and SQL (`SqliteGraph`).
- `graph_reader()` uses Neo4j only when it is reachable **and** its last sync matches SQLite's
  node and edge counts; otherwise SQLite. `GRAPH_BACKEND=sqlite|neo4j|auto` overrides this.
- The evaluation harness keeps reading SQLite, so results never depend on whether Neo4j is up.
- Statistics (co-change counts, ownership, as-of history) stay in SQL: they are aggregations over
  time, not graph walks.

## Consequences

- Neo4j is optional: without Docker or before `make graph-sync`, everything works on SQLite.
- Parity is tested: `tests/test_graphstore.py` (integration) syncs the fixture graph into a
  throwaway namespace and checks that every read and the Map, impact and "why" outputs are
  identical on both stores.
- A new infrastructure dependency (Docker image `neo4j:5.26.32-community`, about 1 GB of memory
  with the configured heap and page cache).
- Writes after a sync (an upload, a new ingest) make Neo4j stale until the next sync. Uploads
  re-sync in the background and ingest re-syncs at the end; until then reads fall back to SQLite.
