import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { fmtDate, pct } from "./ui";

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows every view as a tab", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    renderApp();
    expect(screen.getByRole("heading", { level: 1, name: "Hindsight" })).toBeDefined();
    for (const name of ["Overview", "Ask why", "Impact", "Map", "Knowledge at risk", "Accuracy"]) {
      expect(screen.getByRole("button", { name })).toBeDefined();
    }
  });

  it("shows an API failure instead of hiding it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response("boom", { status: 500, statusText: "Server Error" })),
      ),
    );
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Accuracy" }));
    expect(await screen.findByText(/500 Server Error/)).toBeDefined();
  });
});

describe("formatting", () => {
  it("formats dates and shares", () => {
    expect(fmtDate(0)).toBe("-");
    expect(fmtDate(1_600_000_000)).toBe("2020-09-13");
    expect(pct(0.6548)).toBe("65%");
  });
});
