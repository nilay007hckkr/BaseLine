import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";
import { api, type GraphResponse } from "../api/client";
import { ErrorBox, Kind, Loading, PROVENANCE_HELP, Provenance, Section } from "../ui";

const KIND_COLOURS: Record<string, string> = {
  Service: "#2563eb",
  Component: "#64748b",
  File: "#0891b2",
  Commit: "#7c3aed",
  PR: "#db2777",
  Issue: "#ea580c",
  Comment: "#ca8a04",
  Review: "#ca8a04",
  Author: "#16a34a",
  Doc: "#475569",
  DocChunk: "#475569",
};
const PROV_COLOURS: Record<string, string> = {
  explicit: "#16a34a",
  api: "#2563eb",
  static: "#7c3aed",
  statistical: "#ea580c",
  runtime: "#0891b2",
  heuristic: "#94a3b8",
};

function Canvas({ data, onPick }: { data: GraphResponse; onPick: (key: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...data.nodes.map((n) => ({
          data: {
            id: n.id,
            label: n.label.length > 40 ? `${n.label.slice(0, 40)}…` : n.label,
            colour: KIND_COLOURS[n.kind] ?? "#64748b",
            centre: n.id === data.center ? 1 : 0,
          },
        })),
        ...data.edges.map((e, i) => ({
          data: {
            id: `e${String(i)}`,
            source: e.source,
            target: e.target,
            colour: PROV_COLOURS[e.provenance] ?? "#94a3b8",
          },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(colour)",
            label: "data(label)",
            "font-size": 9,
            color: "#334155",
            width: 14,
            height: 14,
          },
        },
        { selector: "node[centre = 1]", style: { width: 26, height: 26, "font-weight": "bold" } },
        {
          selector: "edge",
          style: {
            "line-color": "data(colour)",
            "target-arrow-color": "data(colour)",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            width: 1.5,
          },
        },
      ],
      layout: { name: "cose", animate: false },
    });
    cy.on("tap", "node", (event) => {
      onPick((event.target as cytoscape.NodeSingular).id());
    });
    return () => {
      cy.destroy();
    };
  }, [data, onPick]);
  return <div ref={ref} className="graph" />;
}

export function Graph() {
  const [query, setQuery] = useState("checkoutservice");
  const [key, setKey] = useState("service:checkoutservice");
  const [depth, setDepth] = useState(1);
  const hits = useQuery({
    queryKey: ["search", query],
    queryFn: () => api.search(query),
    enabled: query.trim().length >= 2,
  });
  const graph = useQuery({ queryKey: ["graph", key, depth], queryFn: () => api.graph(key, depth) });
  const store = useQuery({ queryKey: ["graph-status"], queryFn: api.graphStatus });

  return (
    <>
      <Section
        title="Visual map"
        hint="Code, commits, pull requests, issues, people and docs, and how they connect. Click a node to centre on it."
      >
        {store.data ? (
          <p className="small muted">
            Served by{" "}
            <span className="badge">{store.data.backend === "neo4j" ? "Neo4j" : "SQLite"}</span> ·{" "}
            {store.data.nodes} nodes, {store.data.edges} links
            {store.data.backend === "neo4j" && store.data.browser_url ? (
              <>
                {" "}
                · explore in the{" "}
                <a href={store.data.browser_url} target="_blank" rel="noreferrer">
                  Neo4j Browser
                </a>{" "}
                (namespace <code>{store.data.namespace}</code>)
              </>
            ) : null}
            {store.data.neo4j_reachable && !store.data.in_sync ? (
              <>
                {" "}
                · Neo4j is out of date, run <code>make graph-sync</code>
              </>
            ) : null}
          </p>
        ) : null}
        <div className="row">
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
            placeholder="Search a service, file, PR number, person…"
            aria-label="Search"
          />
          <select
            value={depth}
            onChange={(e) => {
              setDepth(Number(e.target.value));
            }}
            aria-label="Depth"
            className="narrow"
          >
            <option value={1}>1 step</option>
            <option value={2}>2 steps</option>
          </select>
        </div>
        <div className="chips">
          {(hits.data ?? []).slice(0, 10).map((h) => (
            <button
              key={h.key}
              className={h.key === key ? "chip active" : "chip"}
              onClick={() => {
                setKey(h.key);
              }}
            >
              <Kind value={h.kind} /> {h.label.slice(0, 50)}
            </button>
          ))}
        </div>
        <div className="legend small">
          {Object.entries(PROV_COLOURS).map(([p, colour]) => (
            <span key={p} title={PROVENANCE_HELP[p]}>
              <span className="swatch" style={{ background: colour }} /> {p}
            </span>
          ))}
        </div>
      </Section>
      {graph.isPending ? <Loading /> : null}
      {graph.isError ? <ErrorBox error={graph.error} /> : null}
      {graph.data ? (
        <>
          <Canvas data={graph.data} onPick={setKey} />
          {graph.data.truncated ? (
            <p className="muted small">Large neighbourhood: showing the most recent links only.</p>
          ) : null}
          <Section title={`Links (${String(graph.data.edges.length)})`}>
            <table>
              <thead>
                <tr>
                  <th>From</th>
                  <th>Link</th>
                  <th>To</th>
                  <th>Found by</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {graph.data.edges.slice(0, 60).map((e, i) => (
                  <tr key={`${e.source}-${e.target}-${e.kind}-${String(i)}`}>
                    <td className="small">{e.source}</td>
                    <td className="small">{e.kind}</td>
                    <td className="small">{e.target}</td>
                    <td>
                      <Provenance value={e.provenance} />
                    </td>
                    <td className="small mono">{JSON.stringify(e.evidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </>
      ) : null}
    </>
  );
}
