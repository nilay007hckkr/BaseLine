import { useQuery } from "@tanstack/react-query";
import { api, type ScoreCard } from "../api/client";
import { ErrorBox, Loading, Section } from "../ui";

const MEANING: Record<string, string> = {
  "P@5": "Of the top 5 predicted files, the share that really changed",
  "R@10": "Of the files that really changed, the share found in the top 10",
  "Hit@3": "How often at least one of the top 3 predictions was right",
  "Culprit top-1": "How often the service that really broke was ranked first",
  "Culprit top-3": "How often the service that really broke was in the top 3",
  "Change Hit@1": "How often the change that caused the incident was ranked first",
  "Change Hit@3": "How often the change that caused the incident was in the top 3",
  "Heal rate": "Of the tests a cosmetic change broke, the share healed onto the right control",
  "Cosmetic changes kept green": "Of all cosmetic changes, the share where the test still passed",
  "Defects refused": "Of the real defects, the share where the test failed instead of being healed",
};

const SUITES: Record<string, { title: string; hint: string; unit: string }> = {
  impact: {
    title: "How accurate is the impact check?",
    hint: "Replayed on held-out past commits: one changed file is revealed and the rest must be predicted, using only history from before that commit. Compared with the best simple baseline. 95% confidence intervals.",
    unit: "held-out commits",
  },
  rca: {
    title: "How accurate is incident triage?",
    hint: "Faults injected one at a time into the local demo shop after healthy traffic. Each fault comes with a synthetic deploy of the guilty change and two decoys. Compared with the best simple baseline. 95% confidence intervals.",
    unit: "runs",
  },
  heal: {
    title: "Can tests survive UI changes without hiding real bugs?",
    hint: "The shop's pages are changed in the test browser only: cosmetic changes (renamed labels, classes, wrappers) should be healed; real defects (missing or disabled buttons, server errors, wrong price, the cart bug from issue #3578) must fail. Compared with the best simple baseline. 95% confidence intervals.",
    unit: "runs",
  },
};

function OtherRepos({ cards }: { cards: ScoreCard[] }) {
  const repos = [...new Set(cards.map((c) => c.note))];
  if (!repos.length) return null;
  return (
    <Section
      title="Same check on other public repositories"
      hint="Same protocol and default weights, nothing tuned on these repositories. Shows the impact check is not fitted to the main repository."
    >
      <table>
        <thead>
          <tr>
            <th>Repository</th>
            <th>Metric</th>
            <th>Hindsight (95% CI)</th>
            <th>Best baseline</th>
            <th>n</th>
          </tr>
        </thead>
        <tbody>
          {repos.flatMap((repo) =>
            cards
              .filter((c) => c.note === repo)
              .map((c, i) => (
                <tr key={`${repo}-${c.metric}`}>
                  <td>
                    {i === 0 ? <a href={repo}>{repo.replace("https://github.com/", "")}</a> : null}
                  </td>
                  <td>{c.metric}</td>
                  <td>
                    {c.value.toFixed(3)}{" "}
                    <span className="muted small">
                      [{c.ci_low.toFixed(3)}, {c.ci_high.toFixed(3)}]
                    </span>
                  </td>
                  <td>
                    {c.baseline_value.toFixed(3)}{" "}
                    <span className="muted small">{c.baseline.replaceAll("_", " ")}</span>
                  </td>
                  <td>{c.n}</td>
                </tr>
              )),
          )}
        </tbody>
      </table>
      <p className="muted small">
        Reproduce with <code>make try-repo REPO=&lt;url&gt;</code>.
      </p>
    </Section>
  );
}

function Tiles({ cards }: { cards: ScoreCard[] }) {
  return (
    <div className="tiles">
      {cards.map((card) => (
        <div className="tile" key={card.metric}>
          <div className="tile-label">{card.metric}</div>
          <div className="tile-value">{card.value.toFixed(3)}</div>
          <div className="small">
            95% CI {card.ci_low.toFixed(3)}–{card.ci_high.toFixed(3)} · n = {card.n}
          </div>
          <div className="small muted">
            best baseline ({card.baseline.replaceAll("_", " ")}): {card.baseline_value.toFixed(3)}
          </div>
          <div className="small muted">{MEANING[card.metric] ?? ""}</div>
        </div>
      ))}
    </div>
  );
}

export function Scorecard() {
  const q = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data.length) {
    return (
      <Section title="No results yet">
        <p>
          Run <code>make eval</code> to measure the impact check on the project's own history, and{" "}
          <code>make eval-rca</code> to measure incident triage on the demo shop.
        </p>
      </Section>
    );
  }
  return (
    <>
      {Object.entries(SUITES).map(([suite, meta]) => {
        const cards = q.data.filter((card) => card.suite === suite);
        const first = cards[0];
        if (!first) return null;
        return (
          <Section key={suite} title={meta.title} hint={meta.hint}>
            {first.synthetic ? (
              <p className="small">
                <span className="badge">synthetic</span>{" "}
                {suite === "heal"
                  ? "Page changes are made on purpose, in the test browser only; the shop itself is not changed."
                  : "Faults and deploys are injected on purpose; these are not production incidents."}
              </p>
            ) : null}
            <Tiles cards={cards} />
            {first.note ? (
              <p className="small">
                {suite === "impact" ? (
                  <>
                    Repository: <a href={first.note}>{first.note}</a>
                  </>
                ) : (
                  first.note
                )}
              </p>
            ) : null}
            <p className="muted small">
              n = {first.n} {meta.unit} · reproduce with <code>{first.command}</code> · result file{" "}
              <code>{first.result_file}</code>
            </p>
          </Section>
        );
      })}
      <OtherRepos cards={q.data.filter((card) => card.suite === "impact-other")} />
    </>
  );
}
