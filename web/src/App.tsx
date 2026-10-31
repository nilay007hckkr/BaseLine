import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Repeat, Trophy } from "lucide-react";
import { Suspense, lazy, useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { api, type AppConfig, type Feature, type WorkspaceStatus } from "./api/client";
import { DEMO_CONFIG, InstanceProvider, toInstance, useInstance } from "./instance";
import { ACHIEVEMENTS, AppStateProvider, useApp, type ViewKey } from "./state";
import { Connect, Connecting } from "./views/Connect";
import { Ask } from "./views/Ask";
import { Graph } from "./views/Graph";
import { Home } from "./views/Home";
import { Impact } from "./views/Impact";
import { Incidents } from "./views/Incidents";
import { More, MORE_PAGES } from "./views/More";
import { Scorecard } from "./views/Scorecard";
import { Tests } from "./views/Tests";

const Journey = lazy(() => import("./journey/Journey").then((m) => ({ default: m.Journey })));

/** The top-level pages, in the order of the top bar. */
const TABS: { key: ViewKey; label: string; view: ComponentType; needs?: Feature }[] = [
  { key: "home", label: "Home", view: Home },
  { key: "ask", label: "Ask", view: Ask, needs: "ask" },
  { key: "impact", label: "Impact", view: Impact, needs: "impact" },
  { key: "graph", label: "Map", view: Graph, needs: "map" },
  { key: "incidents", label: "Incidents", view: Incidents, needs: "incidents" },
  { key: "tests", label: "Tests", view: Tests, needs: "tests" },
  { key: "scorecard", label: "Proof", view: Scorecard, needs: "proof" },
];

function RepoChip() {
  const { switchRepo } = useInstance();
  const status = useQuery({ queryKey: ["workspace"], queryFn: api.workspaceStatus });
  if (!switchRepo) return null;
  return (
    <button className="ghost repo-chip" onClick={switchRepo} title="Connect another repository">
      <Repeat size={14} aria-hidden="true" />
      {(status.data?.repo ?? "").replace("https://github.com/", "")}
    </button>
  );
}

function Status() {
  const { xp, level, setOnboarded } = useApp();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15000 });
  return (
    <div className="status">
      <RepoChip />
      <button
        className="ghost"
        onClick={() => {
          setOnboarded(false);
        }}
      >
        Tour
      </button>
      <div className="status-id">
        <div>
          LVL {level.number} · {level.name.toUpperCase()} · {xp} XP
        </div>
        <div className={health.isSuccess ? "live" : "muted"}>
          {health.isSuccess ? "● LIVE" : health.isError ? "○ OFFLINE" : "○ …"}
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
      <Trophy size={18} aria-hidden="true" />
      <span>
        {a.title.toUpperCase()} · +{a.xp} XP
      </span>
    </div>
  );
}

function Shell() {
  const { view, go } = useApp();
  const { features } = useInstance();
  const tabs = TABS.filter((t) => !t.needs || features.includes(t.needs));
  const inMore = MORE_PAGES.some((p) => p.key === view);
  const current = tabs.find((t) => t.key === view);
  const View = current?.view ?? More;
  return (
    <>
      <header className="topbar">
        <button
          className="brand"
          onClick={() => {
            go("home");
          }}
          aria-label="Hindsight home"
        >
          <span className="brand-mark" aria-hidden="true">
            <span>H</span>
          </span>
          <h1 className="pixel" style={{ margin: 0, fontSize: "inherit", fontWeight: 400 }}>
            HINDSIGHT
          </h1>
        </button>
        <nav className="nav" aria-label="Main">
          {tabs.map((t) => (
            <button
              key={t.key}
              className={t.key === view ? "nav-tab active" : "nav-tab"}
              aria-current={t.key === view ? "page" : undefined}
              onClick={() => {
                go(t.key);
              }}
            >
              {t.label}
            </button>
          ))}
          <button
            className={inMore ? "nav-tab active" : "nav-tab"}
            aria-current={inMore ? "page" : undefined}
            onClick={() => {
              go("risk");
            }}
          >
            More
          </button>
        </nav>
        <Status />
      </header>
      <main className="content">
        <View />
      </main>
      <Toast />
    </>
  );
}

const loading = <div className="runner-loading">LOADING…</div>;

function Tour({ connecting }: { connecting?: WorkspaceStatus }) {
  return (
    <Suspense fallback={loading}>
      <Journey connecting={connecting} />
    </Suspense>
  );
}

/** The demo: tour on the first visit, then the dashboard. */
function DemoRoot() {
  const { onboarded } = useApp();
  return onboarded ? <Shell /> : <Tour />;
}

/** A workspace: connect a repository; the tour plays while it is read; then the dashboard. */
function WorkspaceRoot({
  switching,
  setSwitching,
}: {
  switching: boolean;
  setSwitching: (on: boolean) => void;
}) {
  const { onboarded } = useApp();
  const client = useQueryClient();
  const status = useQuery({
    queryKey: ["workspace"],
    queryFn: api.workspaceStatus,
    refetchInterval: (q) => (q.state.data?.job === "running" ? 1500 : false),
  });
  const repo = status.data?.repo ?? null;
  const seen = useRef(repo);
  useEffect(() => {
    if (seen.current !== repo) {
      seen.current = repo;
      void client.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "workspace" });
    }
  }, [repo, client]);

  if (status.isPending) return loading;
  const data = status.data;
  if (data?.job === "running")
    return onboarded ? <Connecting status={data} /> : <Tour connecting={data} />;
  if (!repo || switching) {
    return (
      <Connect
        status={data}
        onBack={
          repo
            ? () => {
                setSwitching(false);
              }
            : undefined
        }
      />
    );
  }
  if (!onboarded) return <Tour />;
  return (
    <>
      {data?.job === "failed" ? (
        // a failed re-connect keeps the previous repository on screen: say so, never silently
        <div className="error" role="alert" style={{ margin: "12px 40px 0" }}>
          Could not connect {(data.job_repo ?? "the repository").replace("https://github.com/", "")}
          : {data.error ?? "unknown error"}. Still showing {repo.replace("https://github.com/", "")}
          .{" "}
          <button
            className="link"
            onClick={() => {
              setSwitching(true);
            }}
          >
            Try again
          </button>
        </div>
      ) : null}
      <Shell />
    </>
  );
}

function WorkspaceApp({ config }: { config: AppConfig }) {
  const [switching, setSwitching] = useState(false);
  const instance = useMemo(
    () =>
      toInstance(config, () => {
        setSwitching(true);
      }),
    [config],
  );
  return (
    <InstanceProvider value={instance}>
      <WorkspaceRoot switching={switching} setSwitching={setSwitching} />
    </InstanceProvider>
  );
}

function Root() {
  const config = useQuery({
    queryKey: ["config"],
    queryFn: api.config,
    retry: false,
    staleTime: Infinity,
  });
  if (config.isPending) return loading;
  const data = config.data ?? DEMO_CONFIG; // an older server, or an error: behave as the demo
  if (data.mode === "workspace") return <WorkspaceApp config={data} />;
  return (
    <InstanceProvider value={toInstance(data)}>
      <DemoRoot />
    </InstanceProvider>
  );
}

export function App() {
  return (
    <AppStateProvider>
      <Root />
    </AppStateProvider>
  );
}
