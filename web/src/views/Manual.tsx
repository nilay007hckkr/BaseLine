import { ArrowRight } from "lucide-react";
import type { Feature } from "../api/client";
import { GLOSSARY } from "../glossary";
import { useInstance } from "../instance";
import { useApp, type ViewKey } from "../state";
import { ColourKey, PageHead } from "../ui";

interface Chapter {
  view: ViewKey;
  needs?: Feature;
  title: string;
  line: string;
  trust: string;
}

const CHAPTERS: Chapter[] = [
  {
    view: "ask",
    needs: "ask",
    title: "Ask why",
    line: "Plain words in, linked sources out.",
    trust: "No source, no answer.",
  },
  {
    view: "impact",
    needs: "impact",
    title: "Impact",
    line: "Files in, services, tests, docs and people out.",
    trust: "Replayed on past changes.",
  },
  {
    view: "graph",
    needs: "map",
    title: "Map",
    line: "Search anything; click a shape.",
    trust: "Every line keeps its evidence.",
  },
  {
    view: "incidents",
    needs: "incidents",
    title: "Incidents",
    line: "What broke, who felt it, the likely change.",
    trust: "Synthetic faults; a lead, not proof.",
  },
  {
    view: "tests",
    needs: "tests",
    title: "Tests",
    line: "Tests from tickets; you approve repairs.",
    trust: "Real bugs always fail.",
  },
  {
    view: "risk",
    needs: "risk",
    title: "Bus factor",
    line: "Files only one person knows.",
    trust: "Commits only, bots ignored.",
  },
  {
    view: "docs",
    needs: "docs",
    title: "Docs",
    line: "Upload Markdown or PDF.",
    trust: "Stays on this machine.",
  },
  {
    view: "settings",
    title: "Settings",
    line: "Pick the model and its order.",
    trust: "Keys stay in .env.",
  },
];

export function Manual() {
  const { go } = useApp();
  const { features } = useInstance();
  const chapters = CHAPTERS.filter((c) => !c.needs || features.includes(c.needs));
  return (
    <>
      <PageHead label="Manual" title="One line each." />
      <div className="cm-label">Pages</div>
      <div className="cm-card cm-rows">
        {chapters.map((c) => (
          <button
            key={c.view}
            className="cm-row cm-row-link"
            onClick={() => {
              go(c.view);
            }}
          >
            <span className="cm-row-title">{c.title}</span>
            <span>{c.line}</span>
            <span className="muted">{c.trust}</span>
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        ))}
      </div>
      <div className="cm-label">Colours · one meaning each</div>
      <ColourKey />
      <div className="cm-label">Keys</div>
      <p className="small muted">
        Tour: <kbd>←</kbd> <kbd>→</kbd> steer · <kbd>↑</kbd> boost · <kbd>space</kbd> next ·{" "}
        <kbd>esc</kbd> skip
      </p>
      <details className="glossary-box">
        <summary className="cm-label">Words ({Object.keys(GLOSSARY).length})</summary>
        <dl className="glossary">
          {Object.entries(GLOSSARY).map(([id, g]) => (
            <div key={id}>
              <dt>{g.term}</dt>
              <dd>{g.plain}</dd>
            </div>
          ))}
        </dl>
      </details>
    </>
  );
}
