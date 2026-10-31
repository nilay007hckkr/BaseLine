import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { Term } from "./glossary";
import { fmtDate, pct } from "./ui";

const KEY = "hindsight.progress.v1";

// FIXTURE payloads for the stubbed API
const DEMO = {
  mode: "demo",
  demo_port: 8000,
  repo: null,
  features: ["ask", "impact", "map", "incidents", "tests", "proof", "risk", "docs", "data"],
};
const WORKSPACE = { ...DEMO, mode: "workspace", features: ["ask", "impact", "map", "proof"] };
const EMPTY = { repo: null, job: "idle", stage: "", progress: 0, measured: false };
const RUNNING = {
  repo: null,
  job: "running",
  job_repo: "https://github.com/acme/shop",
  stage: "Reading git history",
  progress: 0.15,
  measured: false,
};
const LLM = {
  order: ["gemini"],
  allow_external: true,
  active: null,
  providers: [
    {
      name: "gemini",
      label: "Gemini",
      external: true,
      key_env: "GEMINI_API_KEY",
      key_set: false,
      model: "",
      model_env: "GEMINI_MODEL",
      ready: false,
    },
    {
      name: "ollama",
      label: "Ollama (local)",
      external: false,
      key_env: null,
      key_set: false,
      model: "",
      model_env: "OLLAMA_CHAT_MODEL",
      ready: false,
    },
  ],
};

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

function nav() {
  return screen.getByRole("navigation", { name: "Main" });
}

/** Answer the listed paths with JSON; everything else stays pending. */
function stubApi(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const hit = Object.entries(routes).find(([path]) => url.endsWith(path));
      return hit
        ? Promise.resolve(new Response(JSON.stringify(hit[1]), { status: 200 }))
        : new Promise(() => undefined);
    }),
  );
}

function onboarded() {
  window.localStorage.setItem(
    KEY,
    JSON.stringify({ onboarded: true, unlocked: [], theme: "dark" }),
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("first visit", () => {
  it("starts with the tour, and the tour can be skipped to the dashboard", async () => {
    stubApi({ "/api/config": DEMO });
    renderApp();
    const skip = await screen.findByRole("button", { name: /Skip to dashboard/ });
    expect(screen.getByRole("heading", { name: /Fly through the history/ })).toBeDefined();
    fireEvent.click(skip);
    expect(screen.getByRole("heading", { level: 1, name: "HINDSIGHT" })).toBeDefined();
    for (const name of ["Home", "Ask", "Impact", "Map", "Incidents", "Tests", "Proof", "More"]) {
      expect(within(nav()).getByRole("button", { name })).toBeDefined();
    }
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? "{}")).toMatchObject({ onboarded: true });
  });
});

describe("tour without 3D", () => {
  it("steps through the gates as cards and ends on the dashboard", async () => {
    stubApi({ "/api/config": DEMO });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /Launch/ }));
    expect(screen.getByRole("heading", { name: /Why is the code like this\?/ })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByRole("heading", { name: /never makes things up/ })).toBeDefined();
    // a gate not reached yet cannot be opened
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: /Gate 4: Incidents/ }).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Gate 1: Ask why/ }));
    expect(screen.getByRole("heading", { name: /Why is the code like this\?/ })).toBeDefined();
    for (let i = 0; i < 5; i++) fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByRole("heading", { name: /Measured, not claimed/ })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /Enter Hindsight/ }));
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? "{}")).toMatchObject({
      onboarded: true,
      unlocked: ["tour"],
    });
  });
});

describe("dashboard", () => {
  beforeEach(onboarded);

  it("shows an API failure instead of hiding it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response("boom", { status: 500, statusText: "Server Error" })),
      ),
    );
    renderApp();
    fireEvent.click(
      within(await screen.findByRole("navigation", { name: "Main" })).getByRole("button", {
        name: "Proof",
      }),
    );
    expect(await screen.findByText(/500 Server Error/)).toBeDefined();
  });

  it("a home step opens its page with the example filled in", async () => {
    stubApi({ "/api/config": DEMO });
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /Ask why/ }));
    const input = screen.getByLabelText<HTMLInputElement>("Question");
    expect(input.value).toBe("Why was currencyservice rewritten in TypeScript?");
  });

  it("settings show where keys go, never a field to type one", async () => {
    stubApi({ "/api/config": DEMO, "/api/settings/llm": LLM });
    renderApp();
    fireEvent.click(
      within(await screen.findByRole("navigation", { name: "Main" })).getByRole("button", {
        name: "More",
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(await screen.findByText(/Key lives in .env as GEMINI_API_KEY/)).toBeDefined();
    expect(screen.getByText("No key")).toBeDefined();
    expect(screen.queryByLabelText(/key/i)).toBeNull();
  });

  it("an answer about code explains the history, then walks through the code", async () => {
    // FIXTURE answer
    const item = { ts: null, flagged: false, path: [], score: 0, url: "" };
    const answer = {
      status: "answered",
      mode: "llm",
      question: "Why is this here?",
      claims: [
        { text: "The team switched to USD to avoid rounding errors.", cites: ["S1"] },
        { text: "chargeCard() converts the amount, then calls the provider.", cites: ["C1"] },
      ],
      evidence: [
        {
          ...item,
          id: "S1",
          node_key: "commit:abc",
          kind: "Commit",
          span_kind: "message",
          title: "commit abc1234: Charge in USD",
          snippet: "Charge in USD",
        },
        {
          ...item,
          id: "C1",
          node_key: "file:src/pay/charge.js",
          kind: "Code",
          span_kind: "code",
          title: "src/pay/charge.js lines 1-1",
          snippet: "   1  function chargeCard() {}",
        },
      ],
      nearest: [],
      anchors: [],
      checker: "citation ids + lexical support",
      notes: [],
    };
    stubApi({ "/api/config": DEMO, "/api/ask": answer });
    renderApp();
    fireEvent.click(
      within(await screen.findByRole("navigation", { name: "Main" })).getByRole("button", {
        name: "Ask",
      }),
    );
    const input = screen.getByLabelText("Question");
    fireEvent.change(input, { target: { value: "Why is this here?" } });
    const form = input.closest("form");
    if (!form) throw new Error("the question box is not in a form");
    fireEvent.submit(form);
    expect(await screen.findByRole("heading", { name: "What the code does" })).toBeDefined();
    expect(screen.getByRole("heading", { name: "Why it is like this" })).toBeDefined();
    expect(document.querySelector("pre.code")?.textContent).toContain("function chargeCard()");
  });

  it("the map asks for the code structure layer only when it is switched on", async () => {
    const service = { id: "service:checkoutservice", kind: "Service", label: "checkoutservice" };
    const symbol = {
      id: "symbol:src/checkoutservice/main.go#PlaceOrder()@L12",
      kind: "Symbol",
      label: "PlaceOrder()",
    };
    const graph = (nodes: object[]) => ({
      center: service.id,
      nodes,
      edges: [],
      truncated: false,
      backend: "sqlite",
    });
    stubApi({
      "/api/config": DEMO,
      // no key: the server picks where the map starts (any repository, not only the demo shop)
      "/api/graph?depth=1": graph([service]),
      "/api/graph?depth=1&code=true": graph([service, symbol]),
    });
    renderApp();
    fireEvent.click(
      within(await screen.findByRole("navigation", { name: "Main" })).getByRole("button", {
        name: "Map",
      }),
    );
    const toggle = await screen.findByRole("button", { name: /Code structure/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByText(/function or class shown/)).toBeNull();
    fireEvent.click(toggle);
    expect(await screen.findByText(/1 function or class shown/)).toBeDefined();
    const urls = vi
      .mocked(fetch)
      .mock.calls.map(([input]) =>
        typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
      );
    expect(
      urls.filter((u) => u.includes("/api/graph?")).map((u) => u.includes("code=true")),
    ).toEqual([false, true]);
  });
});

describe("workspace", () => {
  it("starts on the connect screen and refuses a non-GitHub URL", async () => {
    stubApi({ "/api/config": WORKSPACE, "/api/workspace/status": EMPTY });
    renderApp();
    const input = await screen.findByLabelText<HTMLInputElement>("Enter your repo");
    const connect = screen.getByRole<HTMLButtonElement>("button", { name: "Connect" });
    expect(connect.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "https://github.com/acme/shop" } });
    expect(connect.disabled).toBe(false);
  });

  it("shows progress while a repository is read", async () => {
    onboarded();
    stubApi({ "/api/config": WORKSPACE, "/api/workspace/status": RUNNING });
    renderApp();
    expect(await screen.findByRole("progressbar")).toBeDefined();
    expect(screen.getByText("acme/shop")).toBeDefined();
    expect(screen.getByText(/Reading git history/)).toBeDefined();
  });

  it("hides the demo-shop pages once a repository is connected", async () => {
    onboarded();
    stubApi({
      "/api/config": WORKSPACE,
      "/api/workspace/status": { ...EMPTY, repo: "https://github.com/acme/shop" },
    });
    renderApp();
    const main = await screen.findByRole("navigation", { name: "Main" });
    expect(within(main).queryByRole("button", { name: "Incidents" })).toBeNull();
    expect(within(main).queryByRole("button", { name: "Tests" })).toBeNull();
    expect(within(main).getByRole("button", { name: "Proof" })).toBeDefined();
  });
});

describe("plain language", () => {
  it("explains a technical word", () => {
    render(<Term id="R@10" />);
    expect(screen.getByRole("tooltip").textContent).toMatch(/top 10/);
  });

  it("formats dates and shares", () => {
    expect(fmtDate(0)).toBe("-");
    expect(fmtDate(1_600_000_000)).toBe("2020-09-13");
    expect(pct(0.6548)).toBe("65%");
  });
});
