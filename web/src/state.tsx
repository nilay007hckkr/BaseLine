import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ViewKey =
  | "home"
  | "ask"
  | "impact"
  | "graph"
  | "risk"
  | "docs"
  | "incidents"
  | "tests"
  | "scorecard"
  | "data"
  | "manual"
  | "profile"
  | "settings";

/** What a quest or the tour hands to a page: an example to fill in, and whether to run it. */
export interface Preset {
  question?: string;
  files?: string[];
  run?: boolean;
}

export const ACHIEVEMENTS = {
  tour: { title: "Trailblazer", text: "Finished the guided tour", xp: 200 },
  ask: { title: "Curious mind", text: "Asked a why question", xp: 100 },
  trick: { title: "Not fooled", text: "Saw Hindsight refuse a trick question", xp: 150 },
  impact: { title: "Blast radius", text: "Checked what a change affects", xp: 100 },
  map: { title: "Cartographer", text: "Explored the knowledge map", xp: 100 },
  risk: { title: "Knowledge keeper", text: "Found code that hangs on one person", xp: 100 },
  // the frozen Incidents and Tests pages (shown only with SHOW_FROZEN=true)
  incident: { title: "First responder", text: "Opened an incident", xp: 100 },
  test: { title: "Gardener", text: "Ran a self-healing test", xp: 100 },
  rate: { title: "Fact checker", text: "Rated an answer", xp: 150 },
  proof: { title: "Show me the numbers", text: "Looked at the measured accuracy", xp: 100 },
} as const;
export type AchievementId = keyof typeof ACHIEVEMENTS;

const LEVELS = ["Seed", "Sprout", "Sapling", "Tree", "Grove", "Forest"];
export const XP_PER_LEVEL = 300;

type Theme = "dark" | "light";

interface Saved {
  unlocked: AchievementId[];
  onboarded: boolean;
  theme: Theme;
}

const KEY = "hindsight.progress.v1";
const DEFAULTS: Saved = { unlocked: [], onboarded: false, theme: "dark" };

// Browser storage is a per-viewer convenience: it may be missing or blocked, and the app must
// work the same without it.
function load(): Saved {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const data = JSON.parse(raw) as Partial<Saved>;
    return {
      unlocked: (data.unlocked ?? []).filter((id): id is AchievementId => id in ACHIEVEMENTS),
      onboarded: data.onboarded === true,
      theme: data.theme === "light" ? "light" : "dark",
    };
  } catch {
    return DEFAULTS;
  }
}

function save(data: Saved) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    // storage unavailable: progress lasts for this visit only
  }
}

interface Toast {
  id: number;
  achievement: AchievementId;
}

interface AppState {
  view: ViewKey;
  go: (view: ViewKey, preset?: Preset) => void;
  takePreset: (view: ViewKey) => Preset | null;
  presetStamp: number;
  unlocked: AchievementId[];
  unlock: (id: AchievementId) => void;
  xp: number;
  level: { name: string; number: number; progress: number };
  onboarded: boolean;
  setOnboarded: (value: boolean) => void;
  theme: Theme;
  toggleTheme: () => void;
  toast: Toast | null;
}

const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [saved, setSaved] = useState<Saved>(load);
  const [view, setView] = useState<ViewKey>("home");
  const [preset, setPreset] = useState<{ view: ViewKey; data: Preset } | null>(null);
  const [presetStamp, setPresetStamp] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    save(saved);
    document.documentElement.dataset.theme = saved.theme;
  }, [saved]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => {
      setToast(null);
    }, 3200);
    return () => {
      window.clearTimeout(timer);
    };
  }, [toast]);

  const go = useCallback((next: ViewKey, data?: Preset) => {
    setView(next);
    setPreset(data ? { view: next, data } : null);
    setPresetStamp((n) => n + 1);
    try {
      window.scrollTo({ top: 0 });
    } catch {
      // not available (tests)
    }
  }, []);

  const takePreset = useCallback(
    (target: ViewKey) => (preset && preset.view === target ? preset.data : null),
    [preset],
  );

  const unlock = useCallback((id: AchievementId) => {
    setSaved((s) => (s.unlocked.includes(id) ? s : { ...s, unlocked: [...s.unlocked, id] }));
  }, []);

  // announce each newly unlocked achievement once
  const seen = useRef<AchievementId[] | null>(null);
  useEffect(() => {
    if (seen.current === null) {
      seen.current = saved.unlocked;
      return;
    }
    const fresh = saved.unlocked.find((id) => !seen.current?.includes(id));
    seen.current = saved.unlocked;
    if (fresh) setToast({ id: Date.now(), achievement: fresh });
  }, [saved.unlocked]);

  const value = useMemo<AppState>(() => {
    const xp = saved.unlocked.reduce((sum, id) => sum + ACHIEVEMENTS[id].xp, 0);
    const number = Math.min(LEVELS.length, 1 + Math.floor(xp / XP_PER_LEVEL));
    return {
      view,
      go,
      takePreset,
      presetStamp,
      unlocked: saved.unlocked,
      unlock,
      xp,
      level: {
        name: LEVELS[number - 1] ?? "Forest",
        number,
        progress: number >= LEVELS.length ? 1 : (xp % XP_PER_LEVEL) / XP_PER_LEVEL,
      },
      onboarded: saved.onboarded,
      setOnboarded: (onboarded: boolean) => {
        setSaved((s) => ({ ...s, onboarded }));
      },
      theme: saved.theme,
      toggleTheme: () => {
        setSaved((s) => ({ ...s, theme: s.theme === "dark" ? "light" : "dark" }));
      },
      toast,
    };
  }, [saved, view, go, takePreset, presetStamp, unlock, toast]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const value = useContext(Ctx);
  if (!value) throw new Error("useApp outside AppStateProvider");
  return value;
}
