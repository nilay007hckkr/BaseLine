import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IncidentBriefPanel } from "./IncidentBrief";

// FIXTURE payload for the stubbed API
const BRIEF = {
  incident_id: 7,
  question: null,
  culprit: "paymentservice",
  status: "answered",
  mode: "llm",
  claims: [{ text: "Code ranked paymentservice as culprit.", cites: ["E1"] }],
  evidence: [
    {
      id: "E1",
      node_key: "incident:7:rank:paymentservice",
      kind: "Ranking",
      span_kind: "runtime",
      title: "paymentservice · culprit",
      url: "",
      snippet: "Code ranked paymentservice as culprit (score 1.20).",
      ts: null,
      flagged: false,
      path: [],
      score: 0,
    },
  ],
  steps: [
    { by: "code", tool: "collect", args: {}, status: "ok", evidence_ids: ["E1"], note: "core" },
    {
      by: "model",
      tool: "impact",
      args: { commit: "deadbee" },
      status: "refused",
      evidence_ids: [],
      note: "only the incident's candidate commits can be checked",
    },
  ],
  checker: "citation ids + lexical support",
  notes: ["phrased by fake; every claim passed the checks"],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IncidentBriefPanel id={7} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("incident brief", () => {
  it("investigates only on click and shows claims, steps and sources", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(BRIEF), { status: 200 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Investigate this incident/ }));
    expect(await screen.findByText("Code ranked paymentservice as culprit.")).toBeDefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/incidents/7/investigate",
      expect.objectContaining({ method: "POST", body: "{}" }),
    );
    expect(screen.getByText(/How it investigated \(2 steps\)/)).toBeDefined();
    expect(screen.getByText(/impact \(commit: deadbee\) → refused/)).toBeDefined();
    expect(screen.getByText("paymentservice · culprit")).toBeDefined();
  });

  it("says not found for an unanswered follow-up and shows API errors", async () => {
    const notFound = {
      ...BRIEF,
      question: "Was the database slow?",
      status: "not_found",
      mode: "none",
      claims: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify(notFound), { status: 200 }))),
    );
    renderPanel();
    fireEvent.change(screen.getByRole("textbox", { name: "Follow-up question" }), {
      target: { value: "Was the database slow?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("Not found in the evidence")).toBeDefined();
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response("boom", { status: 500, statusText: "Server Error" })),
      ),
    );
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Investigate this incident/ }));
    expect(await screen.findByText(/500 Server Error/)).toBeDefined();
  });
});
