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
    title: "Who knows it",
    text: "Code that hangs on one person.",
    example: "Bus factor per service",
    view: "risk",
    color: "c-people",
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

function method(card: ScoreCard, name: string): number | undefined {
  return card.methods.find((m) => m.name === name)?.value;
}

/** The headline numbers, all read from the result files and the data (nothing typed by hand). */
function figures(cards: ScoreCard[], botShare: number | undefined): Figure[] {
  const out: Figure[] = [];
  const r10 = find(cards, "impact", "R@10");
  if (r10) {
    const dir = method(r10, "same_directory");
    const co = method(r10, "cochange_only");
    out.push({
      value: pct(r10.value),
      label: "Affected files found",
      vs: [
        "top 10",
        dir === undefined ? "" : `same folder ${pct(dir)}`,
        co === undefined ? "" : `changed-together ${pct(co)}`,
        `n=${String(r10.n)}`,
      ]
        .filter(Boolean)
        .join(" · "),
    });
  }
  const trick = find(cards, "ask", "Not found on trick questions");
  if (trick) {
    const plain = method(trick, "model_without_sources");
    out.push({
      value: pct(trick.value),
      label: "Trick questions refused",
      vs: [plain === undefined ? "" : `plain model ${pct(plain)}`, `n=${String(trick.n)}`]
        .filter(Boolean)
        .join(" · "),
    });
  }
  const cited = find(cards, "ask", "Linked issue cited");
  if (cited) {
    out.push({
      value: pct(cited.value),
      label: "Right source cited",
      vs: `the linked issue · n=${String(cited.n)}`,
    });
  }
  if (botShare !== undefined && botShare > 0) {
    out.push({
      value: pct(botShare),
      label: "Bot commits set aside",
      vs: "before anything is counted",
    });
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
    bot_share?: number;
    nodes_by_kind?: Record<string, number>;
  } | null;
  const web = (repo.data?.repo as { web?: string } | null)?.web ?? "";
  const name = web.replace("https://github.com/", "");
  const steps = mode === "workspace" ? WORKSPACE_STEPS : DEMO_STEPS;
  const figs = figures(scores.data ?? [], s?.bot_share);
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
