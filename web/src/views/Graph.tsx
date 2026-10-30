import { useQuery } from "@tanstack/react-query";
import { Crosshair, ExternalLink, Pause, Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ForceGraph3DInstance, LinkObject, NodeObject } from "3d-force-graph";
import { api, type GraphResponse } from "../api/client";
import { GLOSSARY, Term } from "../glossary";
import { useApp } from "../state";
import { ErrorBox, Loading, Provenance, Section } from "../ui";

/** Plain names and colours for what the dots are. */
const KINDS: Record<string, { plain: string; color: string }> = {
  Service: { plain: "Service", color: "#4ade80" },
  Component: { plain: "Folder", color: "#86efac" },
  File: { plain: "Code file", color: "#38bdf8" },
  Commit: { plain: "Change (commit)", color: "#a3e635" },
  PR: { plain: "Pull request", color: "#c084fc" },
  Issue: { plain: "Issue", color: "#fb923c" },
  Author: { plain: "Person", color: "#facc15" },
  Doc: { plain: "Document", color: "#f472b6" },
  DocChunk: { plain: "Doc section", color: "#f9a8d4" },
  Test: { plain: "Test", color: "#22d3ee" },
};

/** What each kind of link means, in words. */
const LINKS: Record<string, string> = {
  MODIFIES: "changed",
  PART_OF: "is part of",
  FIXES: "fixes",
  REFERENCES: "mentions",
  CALLS: "calls",
  CO_CHANGES_WITH: "usually changes with",
  BELONGS_TO: "belongs to",
  MENTIONS: "mentions",
  AUTHORED_BY: "was written by",
  COVERS: "tests",
  DERIVED_FROM: "comes from",
  EXERCISES: "uses text from",
  RUNBOOK_FOR: "is a runbook for",
  SUPERSEDES: "replaces",
  ON: "is a comment on",
};

interface MapNode extends NodeObject {
  id: string;
  kind: string;
  label: string;
  url: string;
  center: boolean;
  degree: number;
}

interface MapLink extends LinkObject<MapNode> {
  kind: string;
  provenance: string;
}

function short(text: string, max = 28): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function Graph3D({
  data,
  theme,
  rotating,
  onSelect,
}: {
  data: GraphResponse;
  theme: string;
  rotating: boolean;
  onSelect: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraph3DInstance<MapNode, MapLink> | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const run = { cancelled: false, fit: 0 };
    let observer: ResizeObserver | null = null;
    const degree = new Map<string, number>();
    for (const e of data.edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    const nodes: MapNode[] = data.nodes.map((n) => ({
      id: n.id,
      kind: n.kind,
      label: n.label,
      url: n.url,
      center: n.id === data.center,
      degree: degree.get(n.id) ?? 0,
    }));
    const links: MapLink[] = data.edges.map((e) => ({
      source: e.source,
      target: e.target,
      kind: e.kind,
      provenance: e.provenance,
    }));

    void (async () => {
      try {
        const [{ default: ForceGraph3D }, { default: SpriteText }, THREE] = await Promise.all([
          import("3d-force-graph"),
          import("three-spritetext"),
          import("three"),
        ]);
        if (run.cancelled) return;
        const css = getComputedStyle(document.documentElement);
        const color = (name: string, fallback: string) =>
          css.getPropertyValue(name).trim() || fallback;
        const text = color("--text", "#e8f5ec");
        const g = new ForceGraph3D(el, { controlType: "orbit" }) as unknown as ForceGraph3DInstance<
          MapNode,
          MapLink
        >;
        graphRef.current = g;
        g.width(el.clientWidth)
          .height(el.clientHeight)
          .backgroundColor(color("--bg-2", "#0c1b14"))
          .showNavInfo(false)
          .nodeLabel((n) => `${KINDS[n.kind]?.plain ?? n.kind}: ${n.label}`)
          .nodeColor((n) => KINDS[n.kind]?.color ?? "#94a3b8")
          .nodeVal((n) =>
            n.center ? 12 : n.kind === "Service" ? 5 : 1.5 + Math.min(4, n.degree * 0.3),
          )
          .nodeOpacity(0.95)
          .nodeThreeObjectExtend(true)
          .nodeThreeObject((n) => {
            if (!n.center && n.kind !== "Service") return new THREE.Object3D();
            const label = new SpriteText(short(n.label), n.center ? 5 : 3.5, text);
            label.fontFace = "Nunito Variable, Nunito, sans-serif";
            label.fontWeight = "800";
            label.position.y = n.center ? 11 : 7;
            return label;
          })
          .linkColor((l) => color(`--prov-${l.provenance}`, "#94a3b8"))
          .linkOpacity(0.5)
          .linkWidth((l) => (l.provenance === "heuristic" ? 0.25 : 0.8))
          .linkLabel(
            (l) => `${LINKS[l.kind] ?? l.kind} · ${GLOSSARY[l.provenance]?.term ?? l.provenance}`,
          )
          .linkDirectionalParticles((l) => (l.kind === "CALLS" ? 3 : 0))
          .linkDirectionalParticleWidth(1.8)
          .onNodeClick((n) => {
            onSelect(n.id);
            const x = n.x ?? 0;
            const y = n.y ?? 0;
            const z = n.z ?? 0;
            const ratio = 1 + 70 / Math.max(1, Math.hypot(x, y, z));
            g.cameraPosition({ x: x * ratio, y: y * ratio, z: z * ratio }, { x, y, z }, 1100);
          })
          .cooldownTime(5000)
          .graphData({ nodes, links });
        g.cameraPosition({ x: 0, y: 0, z: 160 });
        // frame the graph soon after it appears, and again once the layout has settled
        run.fit = window.setTimeout(() => {
          g.zoomToFit(800, 30);
        }, 1600);
        g.onEngineStop(() => {
          g.zoomToFit(600, 30);
        });
        const controls = g.controls() as { autoRotate?: boolean; autoRotateSpeed?: number };
        controls.autoRotate = rotating;
        controls.autoRotateSpeed = 0.7;
        observer = new ResizeObserver(() => {
          g.width(el.clientWidth).height(el.clientHeight);
        });
        observer.observe(el);
      } catch {
        if (!run.cancelled) setFailed(true);
      }
    })();

    return () => {
      run.cancelled = true;
      window.clearTimeout(run.fit);
      observer?.disconnect();
      graphRef.current?._destructor();
      graphRef.current = null;
      el.replaceChildren();
    };
    // the rotation switch is applied below without rebuilding the scene
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, theme, onSelect]);

  useEffect(() => {
    const g = graphRef.current;
    if (!g) return;
    (g.controls() as { autoRotate?: boolean }).autoRotate = rotating;
  }, [rotating]);

  if (failed) {
    return (
      <div className="graph3d graph3d-fallback">
        <p>
          3D needs WebGL, which this browser does not offer. The same links are listed in the table
          below.
        </p>
      </div>
    );
  }
  return <div ref={ref} className="graph3d" aria-label="3D map of how items connect" role="img" />;
}

function Details({
  data,
  selected,
  onCentre,
}: {
  data: GraphResponse;
  selected: string;
  onCentre: (id: string) => void;
}) {
  const node = data.nodes.find((n) => n.id === selected);
  if (!node) return null;
  const label = (id: string) => data.nodes.find((n) => n.id === id)?.label ?? id;
  const edges = data.edges
    .filter((e) => e.source === selected || e.target === selected)
    .slice(0, 14);
  return (
    <aside className="map-panel">
      <div className="map-kind" style={{ color: KINDS[node.kind]?.color }}>
        {KINDS[node.kind]?.plain ?? node.kind}
      </div>
      <h3>{node.label}</h3>
      <div className="row">
        {node.url ? (
          <a className="button-like" href={node.url} target="_blank" rel="noreferrer">
            <ExternalLink size={15} aria-hidden="true" /> Open source
          </a>
        ) : null}
        {node.id !== data.center ? (
          <button
            onClick={() => {
              onCentre(node.id);
            }}
          >
            <Crosshair size={15} aria-hidden="true" /> Centre the map here
          </button>
        ) : null}
      </div>
      <p className="small muted">How it connects (and how we know):</p>
      <ul className="map-links">
        {edges.map((e, i) => (
          <li key={`${e.source}-${e.kind}-${e.target}-${String(i)}`}>
            <span>
              {short(label(e.source), 40)} <strong>{LINKS[e.kind] ?? e.kind}</strong>{" "}
              {short(label(e.target), 40)}
            </span>{" "}
            <Provenance value={e.provenance} />
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function Graph() {
  const { theme, unlock } = useApp();
  const [query, setQuery] = useState("checkoutservice");
  const [key, setKey] = useState("service:checkoutservice");
  const [depth, setDepth] = useState(1);
  const [selected, setSelected] = useState(key);
  const [rotating, setRotating] = useState(true);
  const hits = useQuery({
    queryKey: ["search", query],
    queryFn: () => api.search(query),
    enabled: query.trim().length >= 2,
  });
  const graph = useQuery({ queryKey: ["graph", key, depth], queryFn: () => api.graph(key, depth) });
  const store = useQuery({ queryKey: ["graph-status"], queryFn: api.graphStatus });

  useEffect(() => {
    unlock("map");
  }, [unlock]);

  const onSelect = useMemo(
    () => (id: string) => {
      setSelected(id);
    },
    [],
  );
  const kindsShown = [...new Set((graph.data?.nodes ?? []).map((n) => n.kind))];
  const provsShown = [...new Set((graph.data?.edges ?? []).map((e) => e.provenance))];

  return (
    <>
      <Section
        title="See how everything connects"
        hint="Drag to rotate, scroll to zoom, click a dot to see what it is and how we know."
      >
        <div className="row">
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
            placeholder="Search a service, file, pull request or person…"
            aria-label="Search"
          />
          <select
            value={depth}
            onChange={(e) => {
              setDepth(Number(e.target.value));
            }}
            aria-label="How far to look"
            className="narrow"
          >
            <option value={1}>Direct links</option>
            <option value={2}>Two steps out</option>
          </select>
          <button
            className="ghost"
            onClick={() => {
              setRotating(!rotating);
            }}
          >
            {rotating ? (
              <Pause size={16} aria-hidden="true" />
            ) : (
              <Play size={16} aria-hidden="true" />
            )}{" "}
            {rotating ? "Pause rotation" : "Rotate"}
          </button>
        </div>
        <div className="chips">
          {(hits.data ?? []).slice(0, 6).map((h) => (
            <button
              key={h.key}
              className={h.key === key ? "chip small-chip active" : "chip small-chip"}
              onClick={() => {
                setKey(h.key);
                setSelected(h.key);
              }}
            >
              {KINDS[h.kind]?.plain ?? h.kind}: {short(h.label, 44)}
            </button>
          ))}
        </div>
      </Section>
      {graph.isPending ? <Loading /> : null}
      {graph.isError ? <ErrorBox error={graph.error} /> : null}
      {graph.data ? (
        <>
          <div className="map-layout">
            <Graph3D data={graph.data} theme={theme} rotating={rotating} onSelect={onSelect} />
            <Details
              data={graph.data}
              selected={selected}
              onCentre={(id) => {
                setKey(id);
                setSelected(id);
              }}
            />
          </div>
          <div className="legend">
            {kindsShown.map((k) => (
              <span key={k}>
                <span
                  className="swatch round"
                  style={{ background: KINDS[k]?.color ?? "#94a3b8" }}
                />{" "}
                {KINDS[k]?.plain ?? k}
              </span>
            ))}
            <span className="muted">· Lines:</span>
            {provsShown.map((p) => (
              <span key={p}>
                <span className="swatch" style={{ background: `var(--prov-${p})` }} />{" "}
                <Term id={p}>{GLOSSARY[p]?.term ?? p}</Term>
              </span>
            ))}
          </div>
          {graph.data.truncated ? (
            <p className="muted small">Busy item: showing its most recent links only.</p>
          ) : null}
          {store.data ? (
            <p className="small muted">
              {store.data.nodes.toLocaleString()} items and {store.data.edges.toLocaleString()}{" "}
              links in total, served by{" "}
              {store.data.backend === "neo4j" ? "the Neo4j graph database" : "SQLite"}
              {store.data.backend === "neo4j" && store.data.browser_url ? (
                <>
                  {" "}
                  (also explorable in the{" "}
                  <a href={store.data.browser_url} target="_blank" rel="noreferrer">
                    Neo4j Browser
                  </a>
                  )
                </>
              ) : null}
              .
            </p>
          ) : null}
          <details className="card">
            <summary>
              All {graph.data.edges.length} links as a table (with the evidence behind each)
            </summary>
            <table>
              <thead>
                <tr>
                  <th>From</th>
                  <th>Link</th>
                  <th>To</th>
                  <th>
                    <Term id="provenance">How we know</Term>
                  </th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {graph.data.edges.slice(0, 80).map((e, i) => (
                  <tr key={`${e.source}-${e.target}-${e.kind}-${String(i)}`}>
                    <td className="small">{e.source}</td>
                    <td className="small">{LINKS[e.kind] ?? e.kind}</td>
                    <td className="small">{e.target}</td>
                    <td>
                      <Provenance value={e.provenance} />
                    </td>
                    <td className="small mono">{JSON.stringify(e.evidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      ) : null}
    </>
  );
}
