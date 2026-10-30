import { ArrowLeft, ArrowRight, Leaf, Play, SkipForward, Sprout } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useApp, type AchievementId } from "../state";
import { createWorld, type World } from "./world";

interface Stop {
  flag: string;
  title: string;
  body: ReactNode;
  tryLabel?: string;
  achievement?: AchievementId;
  run?: () => Promise<ReactNode>;
}

function pct(v: number): string {
  return `${String(Math.round(v * 100))}%`;
}

const STOPS: Stop[] = [
  {
    flag: "Start",
    title: "Hi, I'm Sprout. Welcome to Hindsight.",
    body: (
      <>
        Hindsight is a <strong>memory for software projects</strong>. It reads a project&apos;s
        whole history (every change, discussion and document) so you can ask it instead of digging.
        Six short stops, about two minutes. Press <kbd>→</kbd> to start.
      </>
    ),
  },
  {
    flag: "Ask why",
    title: "Why is the code like this?",
    body: (
      <>
        Code is full of decisions nobody remembers. Ask in plain English: the answer quotes the real
        commits and discussions, each with a link you can check.
      </>
    ),
    tryLabel: "Ask: why was currencyservice rewritten in TypeScript?",
    achievement: "ask",
    run: async () => {
      const a = await api.ask({ question: "Why was currencyservice rewritten in TypeScript?" });
      const first = a.claims[0];
      const source = a.evidence.find((e) => first?.cites.includes(e.id));
      return a.status === "answered" && first ? (
        <>
          “{first.text}”
          {source ? (
            <>
              {" "}
              <span className="muted">from</span>{" "}
              <a href={source.url} target="_blank" rel="noreferrer">
                {source.title}
              </a>
            </>
          ) : null}
        </>
      ) : (
        <>Not found in the history.</>
      );
    },
  },
  {
    flag: "No guessing",
    title: "It says “not found” instead of making things up",
    body: (
      <>
        A trustworthy assistant knows when it doesn&apos;t know. Ask about something that never
        happened (paymentservice was never rewritten in Rust) and watch it refuse to guess.
      </>
    ),
    tryLabel: "Ask the trick question",
    achievement: "trick",
    run: async () => {
      const a = await api.ask({ question: "Why was paymentservice rewritten in Rust?" });
      return a.status === "not_found" ? (
        <>
          <strong>Not found.</strong> {a.notes[a.notes.length - 1] ?? ""}
        </>
      ) : (
        <>It found an answer; check its sources on the Ask page.</>
      );
    },
  },
  {
    flag: "Impact",
    title: "What will my change affect?",
    body: (
      <>
        Before you change a file, see what else it touches: services, tests, docs and the people who
        know it, each with the reason, learned from past changes.
      </>
    ),
    tryLabel: "Check a change to src/checkoutservice/main.go",
    achievement: "impact",
    run: async () => {
      const r = await api.impact({ files: ["src/checkoutservice/main.go"] });
      const services = r.components.slice(0, 4).map((c) => c.target);
      const files = r.files.slice(0, 2).map((f) => f.target);
      return (
        <>
          Likely affected services: <strong>{services.join(", ") || "none"}</strong>. Files that
          usually change with it: {files.join(", ") || "none"}.
        </>
      );
    },
  },
  {
    flag: "Incidents",
    title: "Which change broke the shop?",
    body: (
      <>
        Hindsight watches the demo shop&apos;s logs. When something breaks, it tells the service
        that broke from the ones that only suffered, and points at the change most likely behind it.
      </>
    ),
    tryLabel: "Show the latest incident",
    achievement: "incident",
    run: async () => {
      const list = await api.incidents();
      const latest = list[0];
      if (!latest) return <>No incidents recorded yet.</>;
      const detail = await api.incident(latest.id);
      return (
        <>
          {list.length} incidents detected (faults were injected on purpose). Latest:{" "}
          {detail.summary}
        </>
      );
    },
  },
  {
    flag: "Healing tests",
    title: "Tests that heal themselves, but never hide a bug",
    body: (
      <>
        Tests are written from real tickets. Rename or restyle a button and the test finds it again;
        break the shop for real and the test fails, every time.
      </>
    ),
    tryLabel: "Show the measured result",
    run: async () => {
      const cards = await api.scorecard();
      const green = cards.find(
        (c) => c.suite === "heal" && c.metric === "Cosmetic changes kept green",
      );
      const refused = cards.find((c) => c.suite === "heal" && c.metric === "Defects refused");
      if (!green || !refused) return <>No healing results yet.</>;
      return (
        <>
          {pct(green.value)} of {green.n} tests stayed green through cosmetic changes;{" "}
          {pct(refused.value)} of {refused.n} real defects were caught.{" "}
          {/(\d+) false heals/.exec(green.note)?.[1] ?? "?"} false heals.
        </>
      );
    },
  },
  {
    flag: "Proof",
    title: "Measured, not claimed",
    body: (
      <>
        Every number in Hindsight comes from a test you can re-run, compared with a simple method.
        Faults and page changes made for testing are labelled synthetic.
      </>
    ),
    tryLabel: "Show the headline numbers",
    achievement: "proof",
    run: async () => {
      const cards = await api.scorecard();
      const r10 = cards.find((c) => c.suite === "impact" && c.metric === "R@10");
      const change = cards.find((c) => c.suite === "rca" && c.metric === "Change Hit@1");
      return (
        <>
          {r10 ? (
            <>
              Impact: {pct(r10.value)} of changed files found in the top 10 (simple method{" "}
              {pct(r10.baseline_value)}, n = {r10.n}).{" "}
            </>
          ) : null}
          {change ? (
            <>
              Incidents: guilty change ranked first in {pct(change.value)} (n = {change.n},
              synthetic).
            </>
          ) : null}
        </>
      );
    },
  },
];

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false; // e.g. no matchMedia in the test environment
  }
}

export function Journey() {
  const { setOnboarded, unlock, go } = useApp();
  const [index, setIndex] = useState(0);
  const [leaves, setLeaves] = useState(0);
  const [result, setResult] = useState<{ index: number; node: ReactNode } | null>(null);
  const [busy, setBusy] = useState(false);
  const [has3d, setHas3d] = useState(true);
  const holder = useRef<HTMLDivElement>(null);
  const world = useRef<World | null>(null);

  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const reducedMotion = prefersReducedMotion();
    const w = createWorld(el, {
      stops: STOPS.map((s) => s.flag),
      onLeaf: () => {
        setLeaves((n) => n + 1);
      },
      reducedMotion,
    });
    world.current = w;
    setHas3d(w !== null);
    w?.goTo(0);
    return () => {
      w?.dispose();
      world.current = null;
    };
  }, []);

  useEffect(() => {
    world.current?.goTo(index);
  }, [index]);

  const finish = useCallback(
    (completed: boolean) => {
      if (completed) unlock("tour");
      setOnboarded(true);
      go("home");
    },
    [go, setOnboarded, unlock],
  );

  const next = useCallback(() => {
    if (index >= STOPS.length - 1) finish(true);
    else setIndex(index + 1);
  }, [index, finish]);

  const back = useCallback(() => {
    setIndex((i) => Math.max(0, i - 1));
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (
        event.target instanceof HTMLElement &&
        ["INPUT", "TEXTAREA"].includes(event.target.tagName)
      ) {
        return;
      }
      if (event.key === "ArrowRight" || event.key === " ") {
        event.preventDefault();
        next();
      } else if (event.key === "ArrowLeft") {
        back();
      } else if (event.key === "Escape") {
        finish(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [next, back, finish]);

  const stop = STOPS[index] ?? STOPS[0];
  if (!stop) return null;

  async function tryIt() {
    if (!stop?.run) return;
    setBusy(true);
    try {
      const node = await stop.run();
      setResult({ index, node });
      if (stop.achievement) unlock(stop.achievement);
    } catch (error) {
      setResult({ index, node: <>Could not reach the server: {String(error)}</> });
    } finally {
      setBusy(false);
    }
  }

  const shown = result?.index === index ? result.node : null;
  const last = index === STOPS.length - 1;

  return (
    <div className="journey">
      <div
        className={has3d ? "journey-world" : "journey-world flat"}
        ref={holder}
        aria-hidden="true"
      />
      <div className="journey-hud">
        <div className="journey-brand">
          <Sprout size={22} aria-hidden="true" /> Hindsight
        </div>
        <div
          className="journey-dots"
          aria-label={`Stop ${String(index + 1)} of ${String(STOPS.length)}`}
        >
          {STOPS.map((s, i) => (
            <button
              key={s.flag}
              className={i === index ? "dot active" : i < index ? "dot done" : "dot"}
              onClick={() => {
                setIndex(i);
              }}
              aria-label={`Go to stop ${String(i + 1)}: ${s.flag}`}
            />
          ))}
        </div>
        <div className="journey-right">
          <span className="journey-leaves" title="Leaves collected on the path">
            <Leaf size={18} aria-hidden="true" /> {leaves}
          </span>
          <button
            className="ghost"
            onClick={() => {
              finish(false);
            }}
          >
            <SkipForward size={16} aria-hidden="true" /> Skip to dashboard
          </button>
        </div>
      </div>
      <section className="journey-card" aria-live="polite">
        <div className="journey-step">
          Stop {index + 1} of {STOPS.length} · {stop.flag}
        </div>
        <h2>{stop.title}</h2>
        <p>{stop.body}</p>
        {stop.run ? (
          <div className="journey-try">
            <button
              className="try"
              onClick={() => {
                void tryIt();
              }}
              disabled={busy}
            >
              <Play size={16} aria-hidden="true" /> {busy ? "Running…" : stop.tryLabel}
            </button>
            {shown ? <div className="journey-result">{shown}</div> : null}
          </div>
        ) : null}
        <div className="journey-nav">
          <button className="ghost" onClick={back} disabled={index === 0}>
            <ArrowLeft size={16} aria-hidden="true" /> Back
          </button>
          <span className="muted small">
            <kbd>←</kbd> <kbd>→</kbd> move · <kbd>Esc</kbd> skip
          </span>
          <button className="primary" onClick={next}>
            {last ? "Enter the dashboard" : "Next stop"} <ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      </section>
    </div>
  );
}
