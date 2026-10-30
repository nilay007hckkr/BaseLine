import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BadgeCheck,
  Lock,
  MessageCircleQuestion,
  Network,
  ShieldQuestion,
  Sparkles,
  Timer,
} from "lucide-react";
import type { ReactNode } from "react";
import { api, type ScoreCard } from "../api/client";
import { Term } from "../glossary";
import { ACHIEVEMENTS, useApp, type AchievementId, type Preset, type ViewKey } from "../state";

interface Quest {
  icon: ReactNode;
  title: string;
  plain: string;
  example: string;
  view: ViewKey;
  preset?: Preset;
  xp: number;
}

const QUESTS: Quest[] = [
  {
    icon: <MessageCircleQuestion size={26} />,
    title: "Why is the code like this?",
    plain:
      "Ask in plain English. The answer quotes the commits, pull requests and issues behind it.",
    example: "Why was currencyservice rewritten in TypeScript?",
    view: "ask",
    preset: { question: "Why was currencyservice rewritten in TypeScript?", run: true },
    xp: ACHIEVEMENTS.ask.xp,
  },
  {
    icon: <Network size={26} />,
    title: "What will my change affect?",
    plain:
      "Name the files you plan to change. See the services, tests, docs and people involved, each with a reason.",
    example: "Changing src/checkoutservice/main.go",
    view: "impact",
    preset: { files: ["src/checkoutservice/main.go"], run: true },
    xp: ACHIEVEMENTS.impact.xp,
  },
  {
    icon: <Activity size={26} />,
    title: "What caused this incident?",
    plain:
      "See which service really broke, which ones only suffered, and the change most likely behind it.",
    example: "Incidents detected in the demo shop",
    view: "incidents",
    xp: ACHIEVEMENTS.incident.xp,
  },
  {
    icon: <ShieldQuestion size={26} />,
    title: "Can it be fooled?",
    plain:
      "Ask about something that never happened. Hindsight says 'not found' instead of making it up.",
    example: "Why was paymentservice rewritten in Rust?",
    view: "ask",
    preset: { question: "Why was paymentservice rewritten in Rust?", run: true },
    xp: ACHIEVEMENTS.trick.xp,
  },
];

function pct(v: number): string {
  return `${String(Math.round(v * 100))}%`;
}

interface Stat {
  title: string;
  value: string;
  sentence: ReactNode;
  vs: string;
  n: number;
  synthetic: boolean;
  term?: string;
}

function card(cards: ScoreCard[], suite: string, metric: string): ScoreCard | undefined {
  return cards.find((c) => c.suite === suite && c.metric === metric);
}

/** Plain-language result tiles, built from the measured result files (never typed by hand). */
function stats(cards: ScoreCard[]): Stat[] {
  const out: Stat[] = [];
  const r10 = card(cards, "impact", "R@10");
  if (r10) {
    out.push({
      title: "Finds what a change affects",
      value: pct(r10.value),
      sentence: (
        <>
          of the files that really changed were in its top 10 guesses (<Term id="R@10" />)
        </>
      ),
      vs: `simple method: ${pct(r10.baseline_value)}`,
      n: r10.n,
      synthetic: false,
    });
  }
  const change = card(cards, "rca", "Change Hit@1");
  if (change) {
    out.push({
      title: "Finds the change behind an incident",
      value: pct(change.value),
      sentence: <>of detected incidents had the guilty change ranked first</>,
      vs: `"most recent deploy" guess: ${pct(change.baseline_value)}`,
      n: change.n,
      synthetic: true,
    });
  }
  const culprit = card(cards, "rca", "Culprit top-1");
  if (culprit) {
    out.push({
      title: "Names the service that broke",
      value: pct(culprit.value),
      sentence: (
        <>
          of injected faults had the real <Term id="culprit" /> ranked first
        </>
      ),
      vs: `simple method: ${pct(culprit.baseline_value)}`,
      n: culprit.n,
      synthetic: true,
    });
  }
  const green = card(cards, "heal", "Cosmetic changes kept green");
  if (green) {
    out.push({
      title: "Tests survive UI changes",
      value: pct(green.value),
      sentence: (
        <>
          of tests stayed green when buttons were renamed or restyled (<Term id="heal" />
          ), {/(\d+) false heals/.exec(green.note)?.[1] ?? "?"}{" "}
          <Term id="falseheal">false heals</Term>
        </>
      ),
      vs: `simple method: ${pct(green.baseline_value)}`,
      n: green.n,
      synthetic: true,
    });
  }
  return out;
}

function StatTile({ stat }: { stat: Stat }) {
  return (
    <div className="stat">
      <div className="stat-title">{stat.title}</div>
      <div className="stat-value">{stat.value}</div>
      <div className="stat-sentence">{stat.sentence}</div>
      <div className="stat-foot">
        <span>{stat.vs}</span>
        <span>
          <Term id="n">n</Term> = {stat.n}
        </span>
        {stat.synthetic ? (
          <span className="badge">
            <Term id="synthetic">synthetic</Term>
          </span>
        ) : null}
      </div>
    </div>
  );
}

function Achievements() {
  const { unlocked } = useApp();
  const ids = Object.keys(ACHIEVEMENTS) as AchievementId[];
  return (
    <div className="badges">
      {ids.map((id) => {
        const a = ACHIEVEMENTS[id];
        const done = unlocked.includes(id);
        return (
          <div key={id} className={done ? "badge-card done" : "badge-card"} title={a.text}>
            {done ? (
              <BadgeCheck size={22} aria-hidden="true" />
            ) : (
              <Lock size={22} aria-hidden="true" />
            )}
            <div>
              <div className="badge-title">{a.title}</div>
              <div className="small muted">
                {a.text} · {a.xp} XP
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Home() {
  const { go } = useApp();
  const scores = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  const repo = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const s = (repo.data?.stats ?? null) as {
    commits?: number;
    nodes_by_kind?: Record<string, number>;
  } | null;
  const web = (repo.data?.repo as { web?: string } | null)?.web ?? "";
  const tiles = stats(scores.data ?? []);
  const others = new Set(
    (scores.data ?? []).filter((c) => c.suite === "impact-other").map((c) => c.note),
  );
  return (
    <>
      <section className="hero">
        <div>
          <div className="eyebrow">
            <Sparkles size={16} aria-hidden="true" /> A memory for software projects
          </div>
          <h2 className="hero-title">Know why code exists before you change it.</h2>
          <p className="hero-text">
            Hindsight reads a project&apos;s whole history (commits, pull requests, issues, docs and
            live logs) and answers three questions, always showing its sources, and saying “not
            found” when the history has no answer.
          </p>
          {s ? (
            <p className="small muted">
              Built from {s.commits?.toLocaleString()} commits,{" "}
              {(s.nodes_by_kind?.PR ?? 0).toLocaleString()} pull requests and{" "}
              {(s.nodes_by_kind?.Issue ?? 0).toLocaleString()} issues of{" "}
              <a href={web} target="_blank" rel="noreferrer">
                {web.replace("https://github.com/", "")}
              </a>
              , a public project.
            </p>
          ) : null}
        </div>
        <button
          className="cta"
          onClick={() => {
            go("ask");
          }}
        >
          <Timer size={20} aria-hidden="true" /> 1-minute check: ask your own question
        </button>
      </section>

      <h3 className="section-title">Quests</h3>
      <div className="quests">
        {QUESTS.map((q) => (
          <button
            key={q.title}
            className="quest"
            onClick={() => {
              go(q.view, q.preset);
            }}
          >
            <span className="quest-icon" aria-hidden="true">
              {q.icon}
            </span>
            <span className="quest-title">{q.title}</span>
            <span className="quest-plain">{q.plain}</span>
            <span className="quest-example">Try: {q.example}</span>
            <span className="quest-xp">+{q.xp} XP</span>
          </button>
        ))}
      </div>

      <h3 className="section-title">Measured, not claimed</h3>
      <p className="muted section-hint">
        Each number comes from a result file in the repository and is compared with a{" "}
        <Term id="baseline">simple method</Term>. Full detail on{" "}
        <button
          className="link"
          onClick={() => {
            go("scorecard");
          }}
        >
          Accuracy
        </button>
        .
      </p>
      {scores.isPending ? <p className="muted">Loading results…</p> : null}
      <div className="stats">
        {tiles.map((t) => (
          <StatTile key={t.title} stat={t} />
        ))}
      </div>
      {others.size ? (
        <p className="small muted">
          The impact check was also measured, unchanged, on {others.size} other public projects (
          {[...others].map((w) => w.replace("https://github.com/", "")).join(", ")}).
        </p>
      ) : null}

      <h3 className="section-title">Achievements</h3>
      <Achievements />
    </>
  );
}
