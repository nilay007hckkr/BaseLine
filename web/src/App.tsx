import { useState } from "react";
import { Ask } from "./views/Ask";
import { Docs } from "./views/Docs";
import { Graph } from "./views/Graph";
import { Impact } from "./views/Impact";
import { Incidents } from "./views/Incidents";
import { Overview } from "./views/Overview";
import { Risk } from "./views/Risk";
import { Scorecard } from "./views/Scorecard";
import { Tests } from "./views/Tests";

const VIEWS = {
  overview: { label: "Overview", view: Overview },
  ask: { label: "Ask why", view: Ask },
  impact: { label: "Impact", view: Impact },
  graph: { label: "Map", view: Graph },
  docs: { label: "Docs", view: Docs },
  incidents: { label: "Incidents", view: Incidents },
  tests: { label: "Tests", view: Tests },
  risk: { label: "Knowledge at risk", view: Risk },
  scorecard: { label: "Accuracy", view: Scorecard },
} as const;

type ViewKey = keyof typeof VIEWS;

export function App() {
  const [active, setActive] = useState<ViewKey>("overview");
  const View = VIEWS[active].view;
  return (
    <div className="app">
      <header>
        <h1>Hindsight</h1>
        <p className="tagline">
          Why is the code like this, and what will a change affect? Every answer links to its
          sources.
        </p>
        <nav>
          {(Object.keys(VIEWS) as ViewKey[]).map((key) => (
            <button
              key={key}
              className={key === active ? "tab active" : "tab"}
              onClick={() => {
                setActive(key);
              }}
            >
              {VIEWS[key].label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        <View />
      </main>
    </div>
  );
}
