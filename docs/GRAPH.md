# The graph in Neo4j

Hindsight's graph (commits, pull requests, issues, files, services, people, docs, and how they
connect) is written to SQLite during ingest and copied into Neo4j by `make graph-sync`. When Neo4j
is running and in sync, the Map, the "why" answers' path (commit → PR → issue → discussion) and the
impact check's structure (components, service calls, doc mentions) are read from Neo4j with Cypher.
Otherwise everything reads SQLite and works the same. Why this split: [ADR 0001](adr/0001-neo4j-graph-layer.md).

## Run it

```bash
make graph-up      # Neo4j 5.26 LTS in Docker; generates a local password into data/neo4j/password
make graph-sync    # copy the graph; `make ingest` re-syncs by itself once the repo has been synced
```

Open http://localhost:7474, user `neo4j`, password from `data/neo4j/password` (or `NEO4J_PASSWORD`
if you set one). `GET /api/graph/status` and the Map tab show which store is answering.

Each repository has its own namespace (`ns` = `<owner>__<name>`), so several can live in one
Neo4j. To add another repository after `make try-repo REPO=<url>`:

```bash
cd backend && DATA_DIR=data/other/<owner>__<name> DB_PATH=data/other/<owner>__<name>/hindsight.db \
  uv run hindsight graph-sync
```

## Model

- Nodes: `(:Node:<Kind> {ns, key, title, url, ts, sid, attrs})`, where Kind is `Commit`, `PR`,
  `Issue`, `File`, `Service`, `Component`, `Author`, `Doc`, `DocChunk`. Commits also carry `is_bot`.
- Relationships keep the type they have in SQLite (`MODIFIES`, `PART_OF`, `FIXES`, `REFERENCES`,
  `CALLS`, `CO_CHANGES_WITH`, `BELONGS_TO`, `MENTIONS`, `AUTHORED_BY`, …) with `provenance`
  (`explicit | api | static | statistical | runtime | heuristic`) and `evidence` (JSON text).
- Issues that a commit fixes but that are not in the GitHub cache yet appear with an empty title.

## Queries to try in the Neo4j Browser

Set the namespace once: `:param ns => 'GoogleCloudPlatform__microservices-demo'`. Each query below
was run against the ingested Online Boutique graph.

What links exist, and how were they found?
```cypher
MATCH (a:Node {ns: $ns})-[r]->(b:Node)
RETURN type(r) AS link, a.kind AS from, b.kind AS to, r.provenance AS provenance, count(*) AS n
ORDER BY link, n DESC
```

Which services call which (read from the code), with the line that shows it:
```cypher
MATCH p = (:Service {ns: $ns})-[:CALLS]->(:Service) RETURN p
```

Why does a file look the way it does: its recent human commits, their pull requests and issues:
```cypher
MATCH (f:File {ns: $ns, key: 'file:src/checkoutservice/main.go'})<-[:MODIFIES]-(c:Commit)-[:PART_OF]->(pr:PR)
WHERE c.is_bot = false
OPTIONAL MATCH (pr)-[:FIXES|REFERENCES]->(i:Issue)
RETURN c.title AS commit, c.ts AS ts, pr.title AS pr, collect(i.title)[..2] AS issues
ORDER BY ts DESC LIMIT 5
```

Files that usually change together with it:
```cypher
MATCH (:File {ns: $ns, key: 'file:src/checkoutservice/main.go'})-[r:CO_CHANGES_WITH]-(g:File)
RETURN g.title AS file, r.weight AS weight ORDER BY weight DESC LIMIT 5
```

Who knows a service best (human commits only):
```cypher
MATCH (:Service {ns: $ns, title: 'cartservice'})<-[:BELONGS_TO]-(:File)<-[:MODIFIES]-(c:Commit)-[:AUTHORED_BY]->(a:Author)
WHERE c.is_bot = false
RETURN a.title AS person, count(DISTINCT c) AS commits, max(c.ts) AS last_ts
ORDER BY commits DESC LIMIT 5
```

How is a fixed issue connected to a service?
```cypher
MATCH (i:Issue {ns: $ns})<-[:FIXES]-(:Node) WITH DISTINCT i ORDER BY i.ts DESC LIMIT 3
MATCH (s:Service {ns: $ns, title: 'frontend'})
MATCH p = shortestPath((i)-[*..6]-(s)) RETURN p
```

Which repositories are loaded:
```cypher
MATCH (n:Node) RETURN n.ns AS namespace, count(*) AS nodes ORDER BY nodes DESC
```
