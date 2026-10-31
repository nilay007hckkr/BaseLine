import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  // the dev server talks to the demo API; HINDSIGHT_API points it at another one (e.g. a workspace
  // instance on :8080)
  const api = loadEnv(mode, ".", "HINDSIGHT_").HINDSIGHT_API || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      proxy: { "/api": api },
    },
    test: {
      environment: "jsdom",
    },
  };
});
