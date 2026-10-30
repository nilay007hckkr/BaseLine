import {
  Activity,
  BookOpen,
  Database,
  FileText,
  FlaskConical,
  Home as HomeIcon,
  Map as MapIcon,
  MessageCircleQuestion,
  Moon,
  Network,
  Sprout,
  Sun,
  Trophy,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { Suspense, lazy, type ComponentType } from "react";
import { ACHIEVEMENTS, AppStateProvider, useApp, type ViewKey } from "./state";
import { Ask } from "./views/Ask";
import { Docs } from "./views/Docs";
import { Graph } from "./views/Graph";
import { Home } from "./views/Home";
import { Impact } from "./views/Impact";
import { Incidents } from "./views/Incidents";
import { Manual } from "./views/Manual";
import { Overview } from "./views/Overview";
import { Risk } from "./views/Risk";
import { Scorecard } from "./views/Scorecard";
import { Tests } from "./views/Tests";

const Journey = lazy(() => import("./journey/Journey").then((m) => ({ default: m.Journey })));

interface ViewDef {
  label: string;
  icon: LucideIcon;
  view: ComponentType;
  blurb: string;
}

export const VIEWS: Record<ViewKey, ViewDef> = {
  home: { label: "Home", icon: HomeIcon, view: Home, blurb: "Your quests and results" },
  ask: {
    label: "Ask why",
    icon: MessageCircleQuestion,
    view: Ask,
    blurb: "Why is the code like this?",
  },
  impact: { label: "Impact", icon: Network, view: Impact, blurb: "What will my change affect?" },
  graph: { label: "Map", icon: MapIcon, view: Graph, blurb: "Explore how everything connects" },
  risk: {
    label: "Knowledge at risk",
    icon: UsersRound,
    view: Risk,
    blurb: "Code only one person knows",
  },
  docs: { label: "Docs", icon: FileText, view: Docs, blurb: "Add runbooks and design docs" },
  incidents: {
    label: "Incidents",
    icon: Activity,
    view: Incidents,
    blurb: "What broke, and which change did it?",
  },
  tests: { label: "Tests", icon: FlaskConical, view: Tests, blurb: "Tests that heal themselves" },
  scorecard: {
    label: "Accuracy",
    icon: Trophy,
    view: Scorecard,
    blurb: "How good is it, measured",
  },
  data: {
    label: "The data",
    icon: Database,
    view: Overview,
    blurb: "What was collected, and how it links",
  },
  manual: { label: "Manual", icon: BookOpen, view: Manual, blurb: "How to use Hindsight" },
};

const GROUPS: { title: string; items: ViewKey[] }[] = [
  { title: "", items: ["home"] },
  { title: "Understand the code", items: ["ask", "impact", "graph", "risk", "docs"] },
  { title: "Keep it running", items: ["incidents", "tests"] },
  { title: "Proof", items: ["scorecard", "data"] },
  { title: "Help", items: ["manual"] },
];

function XpChip() {
  const { xp, level, unlocked } = useApp();
  return (
    <div
      className="xp"
      title={`${String(unlocked.length)} of ${String(Object.keys(ACHIEVEMENTS).length)} achievements`}
    >
      <Sprout size={18} aria-hidden="true" />
      <div>
        <div className="xp-label">
          Level {level.number} · {level.name} · {xp} XP
        </div>
        <div className="xp-bar" aria-hidden="true">
          <span style={{ width: `${String(Math.round(level.progress * 100))}%` }} />
        </div>
      </div>
    </div>
  );
}

function Toast() {
  const { toast } = useApp();
  if (!toast) return null;
  const a = ACHIEVEMENTS[toast.achievement];
  return (
    <div className="toast" role="status" key={toast.id}>
      <Trophy size={20} aria-hidden="true" />
      <div>
        <strong>Achievement unlocked: {a.title}</strong>
        <div className="small">
          {a.text} · +{a.xp} XP
        </div>
      </div>
    </div>
  );
}

function Shell() {
  const { view, go, theme, toggleTheme, setOnboarded } = useApp();
  const View = VIEWS[view].view;
  return (
    <div className="shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => {
            go("home");
          }}
        >
          <span className="brand-mark" aria-hidden="true">
            <Sprout size={22} />
          </span>
          <span>
            <h1>Hindsight</h1>
            <span className="brand-sub">your project&apos;s memory</span>
          </span>
        </button>
        <nav aria-label="Main">
          {GROUPS.map((group) => (
            <div key={group.title || "start"} className="nav-group">
              {group.title ? <div className="nav-title">{group.title}</div> : null}
              {group.items.map((key) => {
                const def = VIEWS[key];
                const Icon = def.icon;
                return (
                  <button
                    key={key}
                    className={key === view ? "nav-item active" : "nav-item"}
                    aria-current={key === view ? "page" : undefined}
                    onClick={() => {
                      go(key);
                    }}
                  >
                    <Icon size={18} aria-hidden="true" />
                    {def.label}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div>
            <div className="page-title">{VIEWS[view].label}</div>
            <div className="muted small">{VIEWS[view].blurb}</div>
          </div>
          <div className="topbar-actions">
            <XpChip />
            <button
              className="ghost"
              onClick={() => {
                setOnboarded(false);
              }}
            >
              Replay tour
            </button>
            <button
              className="ghost icon-only"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Light theme" : "Dark theme"}
            >
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </header>
        <main className="content">
          <View />
        </main>
      </div>
      <Toast />
    </div>
  );
}

function Root() {
  const { onboarded } = useApp();
  if (!onboarded) {
    return (
      <Suspense fallback={<div className="journey-loading">Loading the tour…</div>}>
        <Journey />
      </Suspense>
    );
  }
  return <Shell />;
}

export function App() {
  return (
    <AppStateProvider>
      <Root />
    </AppStateProvider>
  );
}
