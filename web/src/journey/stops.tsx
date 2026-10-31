import type { ReactNode } from "react";
import type { Api } from "../api/client";
import type { AchievementId } from "../state";

/** One gate of the onboarding run: what it explains, and a live example to try. */
export interface Stop {
  gate: string;
  title: string;
  text: string;
  tryLabel: string;
  achievement?: AchievementId;
  run: (hs: Api) => Promise<ReactNode>;
}

function pct(v: number): string {
  return `${String(Math.round(v * 100))}%`;
}

export const STOPS: Stop[] = [
  {
    gate: "Ask why",
    title: "Why is the code like this?",
    text: "Ask in plain words. Every answer links its source.",
    tryLabel: "Ask why currencyservice was rewritten in TypeScript",
    achievement: "ask",
    run: async (hs) => {
      const a = await hs.ask({ question: "Why was currencyservice rewritten in TypeScript?" });
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
    gate: "No guessing",
    title: "It says not found, never makes things up.",
    text: "No source, no answer. It says not found.",
    tryLabel: "Ask the trick question",
    achievement: "trick",
    run: async (hs) => {
      const a = await hs.ask({ question: "Why was paymentservice rewritten in Rust?" });
      return a.status === "not_found" ? (
        <>
          <strong>NOT FOUND.</strong> {a.notes[a.notes.length - 1] ?? ""}
        </>
      ) : (
        <>It found an answer; check its sources on the Ask page.</>
      );
    },
  },
  {
    gate: "Impact",
    title: "What will my change affect?",
    text: "See what a change touches, and why.",
    tryLabel: "Check a change to checkoutservice/main.go",
    achievement: "impact",
    run: async (hs) => {
      const r = await hs.impact({ files: ["src/checkoutservice/main.go"] });
      const services = r.components.slice(0, 4).map((c) => c.target);
      return (
        <>
          Likely affected: <strong>{services.join(", ") || "none"}</strong>.
        </>
      );
    },
  },
  {
    gate: "Map",
    title: "How do the services connect?",
    text: "Calls read from the source code, each link with its evidence.",
    tryLabel: "Show the services and their calls",
    achievement: "map",
    run: async (hs) => {
      const g = await hs.graph("", 1, false, true);
      const services = g.nodes.filter((n) => n.kind === "Service").length;
      const calls = g.edges.filter((e) => e.kind === "CALLS").length;
      if (!services) return <>No services found in this repository.</>;
      return (
        <>
          <strong>{services}</strong> services and <strong>{calls}</strong> calls between them,
          found in the code.
        </>
      );
    },
  },
  {
    gate: "Who knows it",
    title: "Which code hangs on one person?",
    text: "One author behind most changes, and whether they are still around.",
    tryLabel: "Show the knowledge at risk",
    achievement: "risk",
    run: async (hs) => {
      const r = await hs.risk();
      const weakest = [...r.components].sort((a, b) => a.bus_factor - b.bus_factor)[0];
      return (
        <>
          <strong>{r.files.length}</strong> files hang on one person
          {weakest ? (
            <>
              ; <strong>{weakest.component}</strong> has a bus factor of {weakest.bus_factor}
            </>
          ) : null}
          .
        </>
      );
    },
  },
  {
    gate: "Proof",
    title: "Measured, not claimed.",
    text: "Every number re-runs against simple methods.",
    tryLabel: "Show the headline numbers",
    achievement: "proof",
    run: async (hs) => {
      const cards = await hs.scorecard();
      const r10 = cards.find((c) => c.suite === "impact" && c.metric === "R@10");
      const trick = cards.find(
        (c) => c.suite === "ask" && c.metric === "Not found on trick questions",
      );
      if (!r10 && !trick) return <>No results yet.</>;
      return (
        <>
          {r10 ? (
            <>
              Impact: {pct(r10.value)} of changed files in the top 10 (simple method{" "}
              {pct(r10.baseline_value)}, n={r10.n}).{" "}
            </>
          ) : null}
          {trick ? (
            <>
              Ask: {pct(trick.value)} of trick questions answered “not found” (n={trick.n}).
            </>
          ) : null}
        </>
      );
    },
  },
];
