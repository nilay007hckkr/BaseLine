import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Incident } from "../api/client";
import { ErrorBox, Loading, ScoreBar, Section, fmtDate } from "../ui";

type Lane = Incident["timeline"][string];

function clock(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(11, 19);
}

function Lane({
  name,
  points,
  start,
  end,
  role,
}: {
  name: string;
  points: Lane;
  start: number;
  end: number;
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
      </svg>
    </div>
  );
}

function Detail({ id }: { id: number }) {
  const q = useQuery({ queryKey: ["incident", id], queryFn: () => api.incident(id) });
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
      <Section
        title={`Incident #${String(inc.id)}`}
        hint={`Opened ${fmtDate(inc.opened_at)} ${clock(inc.opened_at)} UTC · ${inc.closed_at ? `closed ${clock(inc.closed_at)}` : "still open"} · source: ${inc.source}`}
      >
        <p>{inc.summary}</p>
      </Section>
      <Section
        title="Timeline"
        hint="Log volume per service in 10-second windows; errors in red. The marker shows when the incident opened."
      >
        <div className="lanes">
          {lanes.map(([name, points]) => (
            <Lane
              key={name}
              name={name}
              points={points}
              start={start}
              end={end}
              role={roles[name]}
            />
          ))}
          <div
            className="lane-marker"
            style={{
              left: `calc(140px + ${String(((inc.opened_at - start) / (end - start)) * 100)}% * (1 - 140 / 100%))`,
            }}
          />
        </div>
      </Section>
      <div className="grid">
        <Section
          title="Culprit or victim?"
          hint="Errors that name another service move suspicion to it. The service that logs the most errors is often a victim."
        >
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
                  {r.templates.length ? <li>patterns: {r.templates.join(", ")}</li> : null}
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
        <Section
          title="Which change might have caused it?"
          hint="Deploys in the 24 hours before the incident. This is correlation, not proof."
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
      </div>
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
      <Section
        title="Incidents"
        hint="Detected from the shop's logs: new error patterns and error spikes, grouped when they happen within two minutes."
      >
        {list.data.length === 0 ? (
          <p className="muted">
            No incidents yet. Run <code>make collect</code> while the shop runs, or{" "}
            <code>make demo-replay</code>.
          </p>
        ) : (
          <div className="chips">
            {list.data.map((i) => (
              <button
                key={i.id}
                className={i.id === current ? "chip active" : "chip"}
                onClick={() => {
                  setSelected(i.id);
                }}
              >
                #{i.id} · {fmtDate(i.opened_at)} {clock(i.opened_at)} · {i.culprit ?? "?"}
              </button>
            ))}
          </div>
        )}
      </Section>
      {current !== null ? <Detail id={current} /> : null}
    </>
  );
}
