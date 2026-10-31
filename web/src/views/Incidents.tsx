import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, GitCommitHorizontal, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Term } from "../glossary";
import { useApp } from "../state";
import { api, type Incident } from "../api/client";
import { ErrorBox, Loading, PageHead, ScoreBar, Section, SubTabs, fmtDate } from "../ui";
import { IncidentBriefPanel } from "./IncidentBrief";

type Lane = Incident["timeline"][string];

function clock(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(11, 19);
}

function Lane({
  name,
  points,
  start,
  end,
  marker,
  role,
}: {
  name: string;
  points: Lane;
  start: number;
  end: number;
  marker: number;
  role?: string;
}) {
  const width = 600;
  const span = Math.max(1, end - start);
  const max = Math.max(1, ...points.map((p) => p.count));
  return (
    <div className="lane">
      <span className={`lane-name ${role ?? ""}`}>{name}</span>
      <svg
        viewBox={`0 0 ${String(width)} 24`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${name} log volume`}
      >
        {points.map((p) => {
          const x = ((p.ts - start) / span) * width;
          const w = Math.max(1.5, (10 / span) * width);
          const total = (p.count / max) * 22;
          const errors = (p.errors / max) * 22;
          return (
            <g key={p.ts}>
              <rect x={x} y={24 - total} width={w} height={total} className="bar-total" />
              <rect x={x} y={24 - errors} width={w} height={errors} className="bar-errors" />
            </g>
          );
        })}
        <line
          x1={((marker - start) / span) * width}
          x2={((marker - start) / span) * width}
          y1={0}
          y2={24}
          className="lane-opened"
        />
      </svg>
    </div>
  );
}

function minutes(seconds: number): string {
  const m = Math.round(seconds / 60);
  return m < 1 ? "under a minute" : `${String(m)} min`;
}

/** What broke → who felt it → the change most likely behind it, in one line. */
function Story({ incident }: { incident: Incident }) {
  const culprit = incident.ranking.find((r) => r.role === "culprit");
  const victims = incident.ranking.filter((r) => r.role !== "culprit");
  const top = incident.candidates[0];
  const lasted = (incident.closed_at ?? incident.updated_at) - incident.opened_at;
  const namedBy = (culprit?.evidence.named_by ?? []) as { service: string; errors: number }[];
  return (
    <section style={{ marginBottom: 28 }}>
      <div className="label">
        Incident #{incident.id} · {fmtDate(incident.opened_at)} {clock(incident.opened_at)} UTC
      </div>
      <p className="muted small">
        {incident.closed_at ? `Lasted ${minutes(lasted)}` : "Still open"} ·{" "}
        <Term id="synthetic">synthetic</Term> fault
      </p>
      <div className="story">
        <div className="story-step broke">
          <div className="story-label">
            <AlertTriangle size={16} aria-hidden="true" /> What broke
          </div>
          <div className="story-main">{culprit?.service ?? "unclear"}</div>
          <div className="small muted">
            <Term id="culprit">culprit</Term>
            {namedBy.length
              ? ` · named in ${String(namedBy.reduce((n, x) => n + x.errors, 0))} errors`
              : " · its own logs changed first"}
          </div>
        </div>
        <ArrowRight className="story-arrow" aria-hidden="true" />
        <div className="story-step felt">
          <div className="story-label">
            <Users size={16} aria-hidden="true" /> Who felt it
          </div>
          <div className="story-main">
            {victims.length ? victims.map((v) => v.service).join(", ") : "no other service"}
          </div>
          <div className="small muted">
            <Term id="victim">victims</Term> · errors caused by the culprit
          </div>
        </div>
        <ArrowRight className="story-arrow" aria-hidden="true" />
        <div className="story-step change">
          <div className="story-label">
            <GitCommitHorizontal size={16} aria-hidden="true" /> Most likely change
          </div>
          {top ? (
            <>
              <div className="story-main">
                <a href={top.url} target="_blank" rel="noreferrer">
                  {top.title}
                </a>
              </div>
              <div className="small muted">
                {minutes(Number(top.evidence.gap_s))} before
                {top.evidence.touches_culprit ? " · touched it" : ""}
                {top.synthetic ? " · synthetic" : ""} · a lead
              </div>
            </>
          ) : (
            <div className="story-main muted">no deploys recorded</div>
          )}
        </div>
      </div>
    </section>
  );
}

type DetailTab = "brief" | "timeline" | "why" | "changes" | "patterns" | "runbook";
const DETAIL_TABS: { key: DetailTab; label: string }[] = [
  { key: "brief", label: "Brief" },
  { key: "timeline", label: "Timeline" },
  { key: "why", label: "Why" },
  { key: "changes", label: "Changes" },
  { key: "patterns", label: "Patterns" },
  { key: "runbook", label: "Runbook" },
];

function Detail({ id }: { id: number }) {
  const { unlock } = useApp();
  const [tab, setTab] = useState<DetailTab>("brief");
  const q = useQuery({ queryKey: ["incident", id], queryFn: () => api.incident(id) });
  const loaded = q.isSuccess;
  useEffect(() => {
    if (loaded) unlock("incident");
  }, [loaded, unlock]);
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} />;
  const inc = q.data;
  const roles = Object.fromEntries(inc.ranking.map((r) => [r.service, r.role]));
  const start = inc.opened_at - 300;
  const end = (inc.closed_at ?? inc.updated_at) + 60;
  const lanes = Object.entries(inc.timeline).sort(
    ([a], [b]) => (roles[a] ? 0 : 1) - (roles[b] ? 0 : 1) || a.localeCompare(b),
  );
  return (
    <>
      <Story incident={inc} />
      <SubTabs tabs={DETAIL_TABS} value={tab} onChange={setTab} />
      {tab === "brief" ? <IncidentBriefPanel key={id} id={id} /> : null}
      {tab === "timeline" ? (
        <Section
          title="Timeline"
          hint="Log lines per service every 10 s. Red: errors. Green line: opened."
        >
          <div className="lanes">
            {lanes.map(([name, points]) => (
              <Lane
                key={name}
                name={name}
                points={points}
                start={start}
                end={end}
                marker={inc.opened_at}
                role={roles[name]}
              />
            ))}
          </div>
        </Section>
      ) : null}
      {tab === "why" ? (
        <Section title="Why this service?" hint="Errors that name another service point at it.">
          <ul className="plain">
            {inc.ranking.map((r) => (
              <li key={r.service} className="impact-item">
                <div className="impact-head">
                  <ScoreBar value={Math.max(0, Math.min(1, r.score))} />
                  <strong>{r.service}</strong>
                  <span className={`badge role-${r.role}`}>{r.role}</span>
                </div>
                <ul className="reasons">
                  {(r.evidence.named_by as { service: string; errors: number }[] | undefined)?.map(
                    (n) => (
                      <li key={n.service}>
                        named in {n.errors} error lines from {n.service}
                      </li>
                    ),
                  )}
                  {r.evidence.spike_z ? (
                    <li>error spike, robust z = {Number(r.evidence.spike_z).toFixed(1)}</li>
                  ) : null}
                  {r.templates.length ? <li>new log patterns: {r.templates.length}</li> : null}
                  {r.samples.slice(0, 2).map((s) => (
                    <li key={s} className="mono small">
                      {s}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
      {tab === "changes" ? (
        <Section
          title="Recent changes, most likely first"
          hint="Deploys in the 24 h before, ranked. A lead, not proof."
        >
          {inc.candidates.length === 0 ? (
            <p className="muted">No deploys recorded before this incident.</p>
          ) : (
            <ul className="plain">
              {inc.candidates.map((c) => (
                <li key={c.commit} className="impact-item">
                  <div className="impact-head">
                    <ScoreBar value={c.score} />
                    <a href={c.url} target="_blank" rel="noreferrer">
                      {c.commit.slice(0, 7)} {c.title}
                    </a>
                    <span className="badge">candidate</span>
                    {c.synthetic ? (
                      <span className="badge warn-badge">synthetic deploy</span>
                    ) : null}
                  </div>
                  <ul className="reasons">
                    <li>deployed {Math.round(Number(c.evidence.gap_s) / 60)} min before</li>
                    <li>
                      {c.evidence.touches_culprit
                        ? "changes the culprit service"
                        : "does not change the culprit service"}
                    </li>
                    <li>
                      {c.evidence.culprit_in_stored_impact_prediction
                        ? "its impact report (stored at deploy time) listed the culprit"
                        : "its stored impact report did not list the culprit"}
                    </li>
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Section>
      ) : null}
      {tab === "runbook" ? (
        <Section title="Runbook">
          {inc.runbooks.length === 0 ? (
            <p className="muted">No runbook covers the culprit yet. Upload one in Docs.</p>
          ) : (
            <ul className="plain">
              {inc.runbooks.map((rb) => (
                <li key={rb.chunk_key} className="evidence">
                  <a href={rb.url} target="_blank" rel="noreferrer">
                    {rb.title}
                  </a>
                  <p className="snippet">{rb.snippet}</p>
                </li>
              ))}
            </ul>
          )}
        </Section>
      ) : null}
      {tab === "patterns" ? (
        <Section title="Log patterns">
          <table>
            <tbody>
              {inc.templates.map((t) => (
                <tr key={t.id}>
                  <td>{t.id}</td>
                  <td>{t.service}</td>
                  <td className="mono small">{t.text}</td>
                  <td className="num">{t.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}
    </>
  );
}

export function Incidents() {
  const list = useQuery({
    queryKey: ["incidents"],
    queryFn: api.incidents,
    refetchInterval: 10000,
  });
  const [selected, setSelected] = useState<number | null>(null);
  if (list.isPending) return <Loading />;
  if (list.isError) return <ErrorBox error={list.error} />;
  const current = selected ?? list.data[0]?.id ?? null;
  return (
    <>
      <PageHead
        label="Incidents"
        title="What broke, and which change did it?"
        lede="Faults injected on purpose into the demo shop."
      />
      {list.data.length === 0 ? (
        <p className="muted">
          No incidents yet. Run <code>make collect</code> while the shop runs, or{" "}
          <code>make demo-replay</code>.
        </p>
      ) : (
        <div className="inc-layout">
          <nav className="inc-list" aria-label="Incidents">
            {list.data.map((i) => (
              <button
                key={i.id}
                className={i.id === current ? "inc-item active" : "inc-item"}
                onClick={() => {
                  setSelected(i.id);
                }}
              >
                <span>
                  #{i.id} {clock(i.opened_at).slice(0, 5)}
                </span>
                <span>{(i.culprit ?? "?").replace("service", "")}</span>
              </button>
            ))}
          </nav>
          <div>{current !== null ? <Detail id={current} /> : null}</div>
        </div>
      )}
    </>
  );
}
