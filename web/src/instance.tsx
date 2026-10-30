import { createContext, useContext, type ReactNode } from "react";
import { api, makeApi, type Api, type AppConfig, type Feature } from "./api/client";

/** What this server is: the demo (Online Boutique) or a workspace for your own repository. */
export interface Instance {
  mode: AppConfig["mode"];
  features: Feature[];
  /** The API the tour uses: always the demo's, so its examples work before your repo is ready. */
  demo: Api;
  /** Link to the demo dashboard. */
  demoUrl: string;
  /** Open the connect screen (workspace only). */
  switchRepo?: () => void;
}

const ALL: Feature[] = [
  "ask",
  "impact",
  "map",
  "incidents",
  "tests",
  "proof",
  "risk",
  "docs",
  "data",
];
export const DEMO_CONFIG: AppConfig = { mode: "demo", demo_port: 8000, repo: null, features: ALL };

export function toInstance(config: AppConfig, switchRepo?: () => void): Instance {
  if (config.mode === "demo") {
    return { mode: "demo", features: config.features, demo: api, demoUrl: "/" };
  }
  const origin = `${window.location.protocol}//${window.location.hostname}:${String(config.demo_port)}`;
  return {
    mode: "workspace",
    features: config.features,
    demo: makeApi(origin),
    demoUrl: origin,
    switchRepo,
  };
}

const InstanceContext = createContext<Instance>(toInstance(DEMO_CONFIG));

export function InstanceProvider({ value, children }: { value: Instance; children: ReactNode }) {
  return <InstanceContext.Provider value={value}>{children}</InstanceContext.Provider>;
}

export function useInstance(): Instance {
  return useContext(InstanceContext);
}
