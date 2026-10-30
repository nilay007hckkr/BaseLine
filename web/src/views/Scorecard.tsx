import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, type ScoreCard } from "../api/client";
import { Term } from "../glossary";
import { useApp } from "../state";
import { ErrorBox, Loading, Section } from "../ui";

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
};

const SUITES: Record<string, { title: string; plain: string; unit: string; synthetic: string }> = {
  impact: {
    title: "What will a change affect?",
    plain:
      "Replayed on the project's own past changes: one changed file is revealed and Hindsight must find the others, using only history from before that change.",
    unit: "past changes",
    synthetic: "",
  },
  rca: {
    title: "What broke, and which change did it?",
    plain:
      "Services in the demo shop were switched off or misconfigured on purpose, one at a time, after healthy traffic. Each fault came with a recorded deploy of the guilty change and two decoys.",
    unit: "injected faults",
    synthetic: "Faults and deploys were made on purpose; these are not production incidents.",
  },
  heal: {
    title: "Do tests survive UI changes without hiding bugs?",
    plain:
      "The shop's pages were changed in the test browser only: cosmetic changes (renamed labels, restyled or wrapped buttons) should be healed; real defects (missing or disabled buttons, server errors, a wrong price, the cart bug from issue #3578) must fail.",
    unit: "test runs",
    synthetic: "Page changes were made on purpose, in the test browser only.",
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
      hint="The impact check, unchanged and with nothing tuned, on two more public projects: it is not fitted to one codebase."
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

export function Scorecard() {
  const { unlock } = useApp();
  const q = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  useEffect(() => {
    unlock("proof");
  }, [unlock]);
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data.length) {
    return (
      <Section title="No results yet">
        <p>
          Run <code>make eval</code> to measure the impact check on the project&apos;s own history,
          and <code>make eval-rca</code> to measure incident triage on the demo shop.
        </p>
      </Section>
    );
  }
  return (
    <>
      <p className="lead">
        Every number here comes from a test anyone can re-run, compared with simple methods that
        need no Hindsight. Hover a bar for details.
      </p>
      {Object.entries(SUITES).map(([suite, meta]) => {
        const cards = q.data.filter((card) => card.suite === suite);
        const first = cards[0];
        if (!first) return null;
        return (
          <Section key={suite} title={meta.title} hint={meta.plain}>
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
            <TableView cards={cards} />
            <p className="muted small">
              Reproduce with <code>{first.command}</code> · result file{" "}
              <code>{first.result_file}</code>
            </p>
          </Section>
        );
      })}
      <OtherRepos cards={q.data.filter((card) => card.suite === "impact-other")} />
    </>
  );
}
