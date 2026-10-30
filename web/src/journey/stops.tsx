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
    gate: "Incidents",
    title: "Which change broke the shop?",
    text: "From the broken service to the change behind it.",
    tryLabel: "Show the latest incident",
    achievement: "incident",
    run: async (hs) => {
      const list = await hs.incidents();
      const latest = list[0];
      if (!latest) return <>No incidents recorded yet.</>;
      const detail = await hs.incident(latest.id);
      return (
        <>
          {list.length} incidents detected (faults injected on purpose). Latest: {detail.summary}
        </>
      );
    },
  },
  {
    gate: "Healing",
    title: "Tests that heal, never hide a bug.",
    text: "Renamed buttons heal. Real bugs still fail.",
    tryLabel: "Show the measured result",
    run: async (hs) => {
      const cards = await hs.scorecard();
      const green = cards.find(
        (c) => c.suite === "heal" && c.metric === "Cosmetic changes kept green",
      );
      const refused = cards.find((c) => c.suite === "heal" && c.metric === "Defects refused");
      if (!green || !refused) return <>No healing results yet.</>;
      return (
        <>
          {pct(green.value)} of {green.n} tests survived cosmetic changes; {pct(refused.value)} of{" "}
          {refused.n} real defects were caught; {/(\d+) false heals/.exec(green.note)?.[1] ?? "?"}{" "}
          false heals.
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
      const change = cards.find((c) => c.suite === "rca" && c.metric === "Change Hit@1");
      return (
        <>
          {r10 ? (
            <>
              Impact: {pct(r10.value)} of changed files in the top 10 (simple method{" "}
              {pct(r10.baseline_value)}, n={r10.n}).{" "}
            </>
          ) : null}
          {change ? (
            <>
              Incidents: guilty change ranked first in {pct(change.value)} (n={change.n},
              synthetic).
            </>
          ) : null}
        </>
      );
    },
  },
];
