import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ScoreCard } from "../api/client";
import { Term } from "../glossary";
import { useInstance } from "../instance";
import { useApp } from "../state";
import { ErrorBox, Loading, PageHead, Section, SubTabs } from "../ui";

/** Plain names for every method a result file compares. */
const METHOD: Record<string, string> = {
  hindsight: "Hindsight",
  cochange_only: "Changed-together only",
  same_directory: "Same folder",
  most_frequent: "Most often changed",
  baseline_most_errors: "Most error lines",
  baseline_largest_increase: "Biggest error jump",
  baseline_most_recent_deploy: "Latest deploy",
  baseline_naive: "Same tag and text",
  baseline_none: "No healing",
  keyword_only: "Keyword search only",
  model_without_sources: "Same model, no sources",
};

/** Plain titles for every metric, with the glossary entry behind it when there is one. */
const METRIC: Record<string, { title: string; term?: string }> = {
  "P@5": { title: "Of its top 5 guesses, the share that really changed", term: "P@5" },
  "R@10": { title: "Of the files that really changed, the share in its top 10", term: "R@10" },
  "Hit@3": { title: "How often at least one of the top 3 was right", term: "Hit@3" },
  "Culprit top-1": { title: "Broken service ranked first", term: "culprit" },
  "Culprit top-3": { title: "Broken service in the top 3", term: "culprit" },
  "Change Hit@1": { title: "Guilty change ranked first" },
  "Change Hit@3": { title: "Guilty change in the top 3" },
  "Heal rate": { title: "Broken tests healed onto the right button", term: "heal" },
  "Cosmetic changes kept green": { title: "Tests still green after UI changes", term: "heal" },
  "Defects refused": { title: "Real bugs caught instead of healed", term: "falseheal" },
  "Not found on trick questions": {
    title: "Trick questions answered “not found”",
    term: "notfound",
  },
  "Linked issue among the sources (file and subject only)": {
    title: "Question names the file and change: the issue behind it is among the sources",
  },
  "Linked issue cited (file and subject only)": {
    title: "Question names the file and change: the issue behind it is cited",
  },
  "Linked issue among the sources": {
    title: "Question names the commit: the issue behind it is among the sources",
  },
  "Linked issue cited": { title: "Question names the commit: the issue behind it is cited" },
};

const SUITES: Record<string, { title: string; plain: string; unit: string; synthetic: string }> = {
  ask: {
    title: "Does it answer why, from the right source?",
    plain:
      "Questions built by code from “Fixes #N” links: the issue is the right source. Trick questions written by hand (committed before they were first scored) must get “not found”.",
    unit: "questions",
    synthetic: "",
  },
  impact: {
    title: "What will a change affect?",
    plain: "Past changes replayed: one file shown, find the rest from earlier history only.",
    unit: "past changes",
    synthetic: "",
  },
  rca: {
    title: "What broke, and which change did it?",
    plain: "Demo-shop services broken on purpose, each with the guilty deploy and two decoys.",
    unit: "injected faults",
    synthetic: "Synthetic faults, not production incidents.",
  },
  heal: {
    title: "Do tests survive UI changes without hiding bugs?",
    plain:
      "Pages changed in the test browser: cosmetic changes should heal, real defects must fail.",
    unit: "test runs",
    synthetic: "Synthetic page changes, test browser only.",
  },
};

function pct(v: number): string {
  return `${String(Math.round(v * 100))}%`;
}

/** One metric: a bar per method with its 95% range, Hindsight highlighted, values at the tips. */
function MethodChart({ card, unit }: { card: ScoreCard; unit: string }) {
  const meta = METRIC[card.metric] ?? { title: card.metric };
  return (
    <figure className="mchart">
      <figcaption className="mchart-title">
        {meta.term ? <Term id={meta.term}>{meta.title}</Term> : meta.title}
      </figcaption>
      <div className="mchart-rows">
        {card.methods.map((m) => {
          const ours = m.name === "hindsight";
          const name = METHOD[m.name] ?? m.name.replaceAll("_", " ");
          const range = m.ci_high > m.ci_low;
          const summary = `${name}: ${pct(m.value)}${range ? `, 95% range ${pct(m.ci_low)} to ${pct(m.ci_high)}` : ""}`;
          return (
            <div key={m.name} className="mchart-row" tabIndex={0} aria-label={summary}>
              <span className={ours ? "mchart-name ours" : "mchart-name"}>{name}</span>
              <span className="mchart-track">
                <span
                  className={ours ? "mchart-bar ours" : "mchart-bar"}
                  style={{ width: `${String(Math.max(0.5, m.value * 100))}%` }}
                />
                {range ? (
                  <span
                    className="mchart-ci"
                    style={{
                      left: `${String(m.ci_low * 100)}%`,
                      width: `${String((m.ci_high - m.ci_low) * 100)}%`,
                    }}
                  />
                ) : null}
                <span className="mchart-tip" role="tooltip">
                  <strong>{name}</strong>: {pct(m.value)}
                  {range ? (
                    <>
                      {" "}
                      (likely between {pct(m.ci_low)} and {pct(m.ci_high)})
                    </>
                  ) : null}
                  <br />
                  based on {card.n} {unit}
                </span>
              </span>
              <span className="mchart-value">{pct(m.value)}</span>
            </div>
          );
        })}
      </div>
      <div className="mchart-row mchart-axis" aria-hidden="true">
        <span />
        <span className="mchart-scale">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </span>
        <span />
      </div>
    </figure>
  );
}

function Legend() {
  return (
    <div className="legend small">
      <span>
        <span className="swatch" style={{ background: "var(--chart-ours)" }} /> Hindsight
      </span>
      <span>
        <span className="swatch" style={{ background: "var(--chart-base)" }} /> Simple methods (
        <Term id="baseline">what you would get without Hindsight</Term>)
      </span>
      <span>
        <span className="legend-ci" /> <Term id="ci">95% range</Term>
      </span>
    </div>
  );
}

function TableView({ cards }: { cards: ScoreCard[] }) {
  return (
    <details className="table-view">
      <summary className="small">Show the numbers as a table</summary>
      <table>
        <thead>
          <tr>
            <th>Measure</th>
            <th>Method</th>
            <th className="num">Value</th>
            <th className="num">95% range</th>
            <th className="num">Cases</th>
          </tr>
        </thead>
        <tbody>
          {cards.flatMap((c) =>
            c.methods.map((m, i) => (
              <tr key={`${c.metric}-${m.name}`}>
                <td>{i === 0 ? (METRIC[c.metric]?.title ?? c.metric) : ""}</td>
                <td>{METHOD[m.name] ?? m.name}</td>
                <td className="num">{m.value.toFixed(3)}</td>
                <td className="num">
                  {m.ci_low.toFixed(3)}–{m.ci_high.toFixed(3)}
                </td>
                <td className="num">{c.n}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </details>
  );
}

function OtherRepos({ cards }: { cards: ScoreCard[] }) {
  const repos = [...new Set(cards.map((c) => c.note))];
  if (!repos.length) return null;
  return (
    <Section
      title="Same check on other public projects"
      hint="Same check, nothing tuned, on other public projects."
    >
      <div className="mcharts">
        {repos.map((repo) => {
          const r10 = cards.find((c) => c.note === repo && c.metric === "R@10");
          if (!r10) return null;
          return (
            <div key={repo}>
              <p className="small">
                <a href={repo} target="_blank" rel="noreferrer">
                  {repo.replace("https://github.com/", "")}
                </a>{" "}
                <span className="muted">· {r10.n} past changes</span>
              </p>
              <MethodChart card={r10} unit="past changes" />
            </div>
          );
        })}
      </div>
      <Legend />
      <TableView cards={cards} />
      <p className="muted small">
        Reproduce with <code>make try-repo REPO=&lt;url&gt;</code>.
      </p>
    </Section>
  );
}

type Tab = "ask" | "impact" | "rca" | "heal" | "elsewhere";
const TABS: { key: Tab; label: string }[] = [
  { key: "ask", label: "Ask" },
  { key: "impact", label: "Impact" },
  { key: "rca", label: "Incidents" },
  { key: "heal", label: "Healing" },
  { key: "elsewhere", label: "Elsewhere" },
];

function Suite({ suite, cards }: { suite: string; cards: ScoreCard[] }) {
  const meta = SUITES[suite];
  const first = cards[0];
  if (!meta || !first) return <p className="muted">No results for this yet.</p>;
  return (
    <>
      <h2 className="thin" style={{ fontSize: "2rem", margin: "0 0 6px" }}>
        {meta.title}
      </h2>
      <p className="page-lede">{meta.plain}</p>
      {meta.synthetic ? (
        <p className="small">
          <span className="badge">
            <Term id="synthetic">synthetic</Term>
          </span>{" "}
          {meta.synthetic}
        </p>
      ) : null}
      <div className="mcharts">
        {cards.map((card) => (
          <MethodChart key={card.metric} card={card} unit={meta.unit} />
        ))}
      </div>
      <Legend />
      {first.note && suite !== "impact" ? <p className="small">{first.note}</p> : null}
      {suite === "ask" && !cards.some((c) => c.metric === "Not found on trick questions") ? (
        <p className="small">
          Trick questions not measured yet: they are written by hand, then committed before they are
          scored.
        </p>
      ) : null}
      <TableView cards={cards} />
      <p className="muted small">
        Reproduce with <code>{first.command}</code> · result file <code>{first.result_file}</code>
      </p>
    </>
  );
}

export function Scorecard() {
  const { unlock } = useApp();
  const [tab, setTab] = useState<Tab>("ask");
  const q = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  const { mode, features } = useInstance();
  const status = useQuery({
    queryKey: ["workspace"],
    queryFn: api.workspaceStatus,
    enabled: mode === "workspace",
  });
  useEffect(() => {
    unlock("proof");
  }, [unlock]);
  // the incident and healing suites belong to the frozen Incidents and Tests pages
  const tabs = TABS.filter((t) => {
    if (t.key === "rca") return features.includes("incidents");
    if (t.key === "heal") return features.includes("tests");
    if (t.key === "elsewhere") return mode === "demo";
    return true;
  });
  return (
    <>
      <PageHead
        label="Proof"
        title="Measured, not claimed."
        lede="Every number re-runs. Grey bars are simple methods."
      />
      <SubTabs tabs={tabs} value={tab} onChange={setTab} />
      {q.isPending ? <Loading /> : null}
      {q.isError ? <ErrorBox error={q.error} /> : null}
      {q.data && !q.data.length ? (
        <p className="muted">
          {mode === "workspace"
            ? (status.data?.measure_note ?? "Measuring on your repo's history…")
            : "No results yet: run make eval."}
        </p>
      ) : null}
      {q.data?.length ? (
        tab === "elsewhere" ? (
          <OtherRepos cards={q.data.filter((card) => card.suite === "impact-other")} />
        ) : (
          <Suite suite={tab} cards={q.data.filter((card) => card.suite === tab)} />
        )
      ) : null}
    </>
  );
}
