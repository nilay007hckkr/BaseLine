import type { ReactNode } from "react";

/** Plain-language meaning of every technical word the dashboard shows. */
export const GLOSSARY: Record<string, { term: string; plain: string }> = {
  provenance: {
    term: "How we know",
    plain:
      "Every link records how it was found: stated in the data, confirmed by GitHub, read from the code, computed from history, seen while running, or guessed by a simple rule.",
  },
  explicit: { term: "Stated", plain: "Written directly in the data, e.g. a diff or 'Fixes #12'." },
  api: { term: "Confirmed by GitHub", plain: "GitHub's own records say so." },
  static: {
    term: "Read from code",
    plain: "Found by reading the source code, without running it.",
  },
  statistical: {
    term: "Learned from history",
    plain: "Computed from past changes, e.g. two files that usually change together.",
  },
  runtime: { term: "Seen running", plain: "Observed while the system was running." },
  codeStructure: {
    term: "Code structure",
    plain:
      "The functions and classes inside each code file, and which ones call which. Read from the source by a parser (graphify), with the file and line for every link. Generated code and guessed links are left out.",
  },
  heuristic: {
    term: "Simple rule",
    plain: "Inferred by a simple rule, e.g. a '#12' mention. The weakest kind of link.",
  },
  "P@5": {
    term: "Precision at 5",
    plain: "Of the 5 files Hindsight flagged first, the share that really changed.",
  },
  "R@10": {
    term: "Recall at 10",
    plain: "Of the files that really changed, the share Hindsight found in its top 10.",
  },
  "Hit@3": {
    term: "Hit at 3",
    plain: "How often at least one of the top 3 guesses was right.",
  },
  ci: {
    term: "95% range",
    plain:
      "The likely range of the true value given how many cases we tested. Narrow = confident; wide = few cases.",
  },
  baseline: {
    term: "Simple method",
    plain:
      "What you would get without Hindsight, e.g. 'files that change most often'. A tool is only useful if it beats this.",
  },
  n: { term: "Number of cases", plain: "How many real cases the number is based on." },
  culprit: { term: "Culprit", plain: "The service that actually broke." },
  victim: {
    term: "Victim",
    plain: "A service that shows errors only because the culprit broke; it is not the cause.",
  },
  impact: {
    term: "Impact",
    plain: "Everything a change is likely to affect: files, services, tests, docs and people.",
  },
  cochange: {
    term: "Co-change",
    plain: "Two files that were usually changed together in the past.",
  },
  notfound: {
    term: "Not found",
    plain:
      "Hindsight answers only from the project's own history. If that history has no answer, it says so instead of guessing.",
  },
  synthetic: {
    term: "Synthetic",
    plain:
      "Made on purpose for testing (e.g. switching a service off). Clearly labelled; not a real production incident.",
  },
  heal: {
    term: "Self-healing test",
    plain:
      "When a button is renamed or restyled, the test finds the same button again instead of failing.",
  },
  falseheal: {
    term: "False heal",
    plain:
      "Healing a test that should have failed, which would hide a real bug. The target is zero.",
  },
  citation: {
    term: "Citation",
    plain: "A link to the exact commit, pull request, issue or document a sentence comes from.",
  },
};

/** A technical word with a plain-language explanation on hover or keyboard focus. */
export function Term({ id, children }: { id: string; children?: ReactNode }) {
  const entry = GLOSSARY[id];
  if (!entry) return <>{children ?? id}</>;
  return (
    <span className="term" tabIndex={0}>
      {children ?? entry.term}
      <span className="term-q" aria-hidden="true">
        ?
      </span>
      <span className="term-tip" role="tooltip">
        <strong>{entry.term}:</strong> {entry.plain}
      </span>
    </span>
  );
}
