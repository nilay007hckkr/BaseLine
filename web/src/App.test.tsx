import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { Term } from "./glossary";
import { fmtDate, pct } from "./ui";

const KEY = "hindsight.progress.v1";

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

function pendingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => undefined)),
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
    pendingFetch();
    renderApp();
    const skip = await screen.findByRole("button", { name: /Skip to dashboard/ });
    expect(screen.getByRole("heading", { name: /Welcome to Hindsight/ })).toBeDefined();
    fireEvent.click(skip);
    expect(screen.getByRole("heading", { level: 1, name: "Hindsight" })).toBeDefined();
    for (const name of [
      "Home",
      "Ask why",
      "Impact",
      "Map",
      "Incidents",
      "Tests",
      "Accuracy",
      "Manual",
    ]) {
      expect(within(nav()).getByRole("button", { name })).toBeDefined();
    }
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? "{}")).toMatchObject({ onboarded: true });
  });
});

describe("dashboard", () => {
  beforeEach(() => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ onboarded: true, unlocked: [], theme: "dark" }),
    );
  });

  it("shows an API failure instead of hiding it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response("boom", { status: 500, statusText: "Server Error" })),
      ),
    );
    renderApp();
    fireEvent.click(within(nav()).getByRole("button", { name: "Accuracy" }));
    expect(await screen.findByText(/500 Server Error/)).toBeDefined();
  });

  it("a quest opens its page with the example filled in", () => {
    pendingFetch();
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: /Can it be fooled\?/ }));
    const input = screen.getByLabelText<HTMLInputElement>("Question");
    expect(input.value).toBe("Why was paymentservice rewritten in Rust?");
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
