import { ArrowRight, Diamond, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { WorkspaceStatus } from "../api/client";
import { useInstance } from "../instance";
import { useApp } from "../state";
import { createRunner, type RunStats, type Runner } from "./runner";
import { STOPS } from "./stops";

type Phase = "intro" | "fly" | "gate";

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false; // e.g. no matchMedia in the test environment
  }
}

function two(n: number): string {
  return String(n).padStart(2, "0");
}

export function Journey({ connecting }: { connecting?: WorkspaceStatus }) {
  const { setOnboarded, unlock, go } = useApp();
  const { demo } = useInstance();
  const [phase, setPhase] = useState<Phase>("intro");
  const [reached, setReached] = useState(-1); // the last gate the ship stopped at
  const [index, setIndex] = useState(0); // the gate card on screen
  const [stats, setStats] = useState<RunStats>({ distance: 0, shards: 0, hits: 0 });
  const [glitch, setGlitch] = useState(0);
  const [result, setResult] = useState<{ index: number; node: ReactNode } | null>(null);
  const [busy, setBusy] = useState(false);
  const [has3d, setHas3d] = useState(true);
  const holder = useRef<HTMLDivElement>(null);
  const runner = useRef<Runner | null>(null);

  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const r = createRunner(el, {
      gates: STOPS.length,
      reducedMotion: prefersReducedMotion(),
      onGate: (i) => {
        setReached(i);
        setIndex(i);
        setPhase("gate");
      },
      onStats: setStats,
      onHit: () => {
        setGlitch((g) => g + 1);
      },
    });
    runner.current = r;
    setHas3d(r !== null);
    return () => {
      r?.dispose();
      runner.current = null;
    };
  }, []);

  const finish = useCallback(
    (completed: boolean) => {
      if (completed) unlock("tour");
      setOnboarded(true);
      go("home");
    },
    [go, setOnboarded, unlock],
  );

  const launch = useCallback(() => {
    if (runner.current) {
      setPhase("fly");
      runner.current.launch();
    } else {
      // no 3D: step straight to the next card
      setReached(reached + 1);
      setIndex(reached + 1);
      setPhase("gate");
    }
  }, [reached]);

  const advance = useCallback(() => {
    if (phase === "fly") return;
    if (phase === "gate" && index < reached) setIndex(index + 1);
    else if (phase === "gate" && reached >= STOPS.length - 1) finish(true);
    else launch();
  }, [phase, index, reached, finish, launch]);

  const back = useCallback(() => {
    if (phase === "gate") setIndex((i) => Math.max(0, i - 1));
  }, [phase]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.repeat) return;
      const target = event.target instanceof HTMLElement ? event.target.tagName : "";
      if (event.key === "Escape") {
        finish(false);
      } else if (event.key === "ArrowRight") {
        advance();
      } else if (event.key === "ArrowLeft") {
        back();
      } else if (
        (event.key === " " || event.key === "Enter") &&
        !["BUTTON", "A", "INPUT", "TEXTAREA"].includes(target)
      ) {
        event.preventDefault();
        advance();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [advance, back, finish]);

  const stop = phase === "gate" ? STOPS[index] : undefined;
  const lastCard = index === STOPS.length - 1;

  async function tryIt() {
    if (!stop) return;
    const at = index;
    setBusy(true);
    try {
      const node = await stop.run(demo);
      setResult({ index: at, node });
      if (stop.achievement) unlock(stop.achievement);
    } catch (error) {
      setResult({ index: at, node: <>Could not reach the server: {String(error)}</> });
    } finally {
      setBusy(false);
    }
  }

  const shown = result?.index === index ? result.node : null;

  return (
    <div className="runner">
      <div
        className={has3d ? "runner-world" : "runner-world flat"}
        ref={holder}
        aria-hidden="true"
      />
      {glitch ? <div key={glitch} className="runner-glitch" aria-hidden="true" /> : null}
      <div className={phase === "fly" ? "runner-shade off" : "runner-shade"} aria-hidden="true" />

      <header className="runner-top">
        <div className="brand runner-brand">
          <span className="brand-mark" aria-hidden="true">
            <span>H</span>
          </span>
          HINDSIGHT
        </div>
        <div className="runner-stats">
          <span>
            <Diamond size={11} aria-hidden="true" /> <strong>{two(stats.shards)}</strong>
          </span>
          <span>
            <strong>{String(Math.floor(stats.distance)).padStart(4, "0")}</strong> M
          </span>
          <span>
            GATE <strong>{two(reached + 1)}</strong>/{two(STOPS.length)}
          </span>
          {stats.hits ? <span className="runner-hits">GLITCH {stats.hits}</span> : null}
          {connecting ? (
            <span className="runner-repo" title={connecting.stage}>
              YOUR REPO <strong>{Math.round(connecting.progress * 100)}%</strong>
            </span>
          ) : null}
        </div>
      </header>

      {phase !== "fly" ? (
        <div className="run-stage">
          {stop ? (
            <section className="run-card" key={index} aria-live="polite">
              <div className="run-num" aria-hidden="true">
                {two(index + 1)}
              </div>
              <div className="run-label">
                Gate {two(index + 1)} · {stop.gate}
              </div>
              <h2 className="run-title">{stop.title}</h2>
              <p className="run-text">{stop.text}</p>
              <button
                className="run-try"
                onClick={() => {
                  void tryIt();
                }}
                disabled={busy}
              >
                <Play size={14} aria-hidden="true" />
                {busy ? "Running…" : `Try it live: ${stop.tryLabel}`}
              </button>
              {shown ? <div className="run-result">{shown}</div> : null}
              {lastCard ? (
                <div className="run-summary">
                  Run complete · {stats.shards} shards · {stats.hits} glitches ·{" "}
                  {Math.floor(stats.distance)} m
                </div>
              ) : null}
              <button className="bar-cta run-go" onClick={advance}>
                {lastCard ? "Enter Hindsight" : "Continue"} <kbd>space</kbd>
              </button>
            </section>
          ) : (
            <section className="run-card" aria-live="polite">
              <div className="run-num" aria-hidden="true">
                00
              </div>
              <div className="run-label">Welcome to Hindsight</div>
              <h2 className="run-title">Fly through the history</h2>
              <p className="run-text">A memory for your codebase. Six gates, one feature each.</p>
              <div className="run-keys">
                <span>
                  <kbd>←</kbd>
                  <kbd>→</kbd> steer
                </span>
                <span>
                  <kbd>↑</kbd> boost
                </span>
                <span>
                  <kbd>esc</kbd> skip
                </span>
              </div>
              <button className="bar-cta run-go" onClick={advance}>
                Launch <kbd>space</kbd>
              </button>
            </section>
          )}
        </div>
      ) : null}

      <footer className="runner-bottom">
        <div className="runner-hint">
          {phase === "fly" ? (
            <>
              <kbd>←</kbd> <kbd>→</kbd> steer · <kbd>↑</kbd> boost
            </>
          ) : (
            <>
              <kbd>←</kbd> <kbd>→</kbd> move · <kbd>esc</kbd> skip
            </>
          )}
        </div>
        <nav className="run-dots" aria-label="Gates">
          {STOPS.map((s, i) => (
            <button
              key={s.gate}
              className={
                phase === "gate" && i === index
                  ? "run-dot active"
                  : i <= reached
                    ? "run-dot done"
                    : "run-dot"
              }
              disabled={phase === "fly" || i > reached}
              aria-label={`Gate ${String(i + 1)}: ${s.gate}`}
              aria-current={phase === "gate" && i === index ? "step" : undefined}
              onClick={() => {
                setIndex(i);
              }}
            />
          ))}
        </nav>
        <button
          className="run-skip"
          aria-label="Skip to dashboard"
          onClick={() => {
            finish(false);
          }}
        >
          Skip <ArrowRight size={16} aria-hidden="true" />
        </button>
      </footer>
    </div>
  );
}
