import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { api, type ScoreCard } from "../api/client";
import { Term } from "../glossary";
import { useInstance } from "../instance";
import { useApp, type Preset, type ViewKey } from "../state";

interface Step {
  title: string;
  text: string;
  example?: string;
  view: ViewKey;
  preset?: Preset;
  color: string; // what the step is about, in the colour key (Manual › Colours)
}

const DEMO_STEPS: Step[] = [
  {
    title: "Ask why",
    text: "Answers from the project's history.",
    example: "Why was currencyservice rewritten in TypeScript?",
    view: "ask",
    preset: { question: "Why was currencyservice rewritten in TypeScript?", run: true },
    color: "c-history",
  },
  {
    title: "See what breaks",
    text: "What a change touches, and why.",
    example: "src/checkoutservice/main.go",
    view: "impact",
    preset: { files: ["src/checkoutservice/main.go"], run: true },
    color: "c-learned",
  },
  {
    title: "Find the cause",
    text: "From an error to the change behind it.",
    example: "The demo shop's incidents",
    view: "incidents",
    color: "c-broken",
  },
];

const WORKSPACE_STEPS: Step[] = [
  { title: "Ask why", text: "Answers from your repo's history.", view: "ask", color: "c-history" },
  {
    title: "See what breaks",
    text: "What a change touches, and why.",
    view: "impact",
    color: "c-learned",
  },
  {
    title: "See the map",
    text: "Code, changes and people, connected.",
    view: "graph",
    color: "c-code",
  },
];

function pct(v: number): string {
  return `${String(Math.round(v * 100))}%`;
}

function find(cards: ScoreCard[], suite: string, metric: string): ScoreCard | undefined {
  return cards.find((c) => c.suite === suite && c.metric === metric);
}

interface Figure {
  value: string;
  label: string;
  vs: string;
}

/** The headline numbers, all read from the result files (nothing typed by hand). */
function figures(cards: ScoreCard[]): Figure[] {
  const out: Figure[] = [];
  const r10 = find(cards, "impact", "R@10");
  if (r10) {
    out.push({
      value: pct(r10.value),
      label: "Files found",
      vs: `top 10 · simple ${pct(r10.baseline_value)} · n=${String(r10.n)}`,
    });
  }
  const change = find(cards, "rca", "Change Hit@1");
  if (change) {
    out.push({
      value: pct(change.value),
      label: "Culprit first",
      vs: `latest deploy ${pct(change.baseline_value)} · n=${String(change.n)} · synthetic`,
    });
  }
  const green = find(cards, "heal", "Cosmetic changes kept green");
  if (green) {
    out.push({
      value: pct(green.value),
      label: "Tests healed",
      vs: `simple ${pct(green.baseline_value)} · n=${String(green.n)} · synthetic`,
    });
    const falseHeals = /(\d+) false heals/.exec(green.note)?.[1];
    if (falseHeals !== undefined) {
      out.push({ value: falseHeals, label: "Bugs hidden", vs: "target 0" });
    }
  }
  return out;
}

export function Home() {
  const { go, level } = useApp();
  const { mode } = useInstance();
  const scores = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  const repo = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const s = (repo.data?.stats ?? null) as {
    commits?: number;
    nodes_by_kind?: Record<string, number>;
  } | null;
  const web = (repo.data?.repo as { web?: string } | null)?.web ?? "";
  const name = web.replace("https://github.com/", "");
  const steps = mode === "workspace" ? WORKSPACE_STEPS : DEMO_STEPS;
  const figs = figures(scores.data ?? []);
  return (
    <>
      <div className="hello">Hi, explorer · {mode === "workspace" && name ? name : level.name}</div>
      <h2 className="hero-title">Know why code exists before you change it.</h2>
      <p className="hero-text">
        Every answer cites its source, or says <Term id="notfound">not found</Term>.
      </p>

      <div className="steps">
        {steps.map((step, i) => (
          <button
            key={step.title}
            className="step"
            onClick={() => {
              go(step.view, step.preset);
            }}
          >
            <span className={`step-num ${step.color}`} aria-hidden="true">
              {i + 1}
            </span>
            <span>
              <span className="step-title">{step.title}</span>
              <span className="step-text">{step.text}</span>
              <span className="step-try">
                {step.example ?? "Open"} <ArrowRight size={14} aria-hidden="true" />
              </span>
            </span>
          </button>
        ))}
      </div>

      {figs.length ? (
        <>
          <div className="cm-label">
            Measured ·{" "}
            <button
              className="link"
              onClick={() => {
                go("scorecard");
              }}
            >
              see the proof
            </button>
          </div>
          <div className="cm-card cm-stats">
            {figs.map((f) => (
              <div key={f.label} className="cm-stat">
                <div className="cm-stat-label">{f.label}</div>
                <div className="cm-stat-value">{f.value}</div>
                <div className="cm-stat-sub">{f.vs}</div>
              </div>
            ))}
          </div>
        </>
      ) : null}

      <button
        className="bar-cta"
        onClick={() => {
          go("ask");
        }}
      >
        Ask your own question <ArrowRight size={20} aria-hidden="true" />
      </button>
      {s ? (
        <p className="small muted" style={{ marginTop: 18 }}>
          {s.commits?.toLocaleString()} commits · {(s.nodes_by_kind?.PR ?? 0).toLocaleString()} pull
          requests · {(s.nodes_by_kind?.Issue ?? 0).toLocaleString()} issues ·{" "}
          <a href={web} target="_blank" rel="noreferrer">
            {name}
          </a>
        </p>
      ) : null}
    </>
  );
}
