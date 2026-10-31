import { useMutation } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { api, type IncidentBrief, type InvestigationStep } from "../api/client";
import { ErrorBox, Section } from "../ui";
import { Evidence } from "./Ask";

function stepLine(step: InvestigationStep): string {
  const args = Object.entries(step.args)
    .map(([k, v]) => `${k}: ${v}`)
    .join(", ");
  return `${step.tool}${args ? ` (${args})` : ""} → ${step.status}${step.note ? `: ${step.note}` : ""}`;
}

function hintFor(brief: IncidentBrief): string | undefined {
  if (brief.mode === "llm") {
    return "Worded by the model. Every line passed the citation check and names only the culprit code ranked.";
  }
  if (brief.mode === "extractive") return "Quoted from the evidence, with no AI rewording.";
  return undefined;
}

function Result({ brief }: { brief: IncidentBrief }) {
  const title =
    brief.status === "not_found"
      ? "Not found in the evidence"
      : brief.question
        ? "Answer"
        : "What happened";
  return (
    <>
      <Section title={title} hint={hintFor(brief)}>
        {brief.question ? <p className="small">Asked: {brief.question}</p> : null}
        {brief.claims.length ? (
          <ul className="claims">
            {brief.claims.map((c) => (
              <li key={c.text}>
                {c.text}{" "}
                {c.cites.map((id) => (
                  <a key={id} className="cite" href={`#ev-${id}`}>
                    {id}
                  </a>
                ))}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">
            Nothing in the evidence answers this, so Hindsight does not guess.
          </p>
        )}
        {brief.notes.length ? <p className="muted small">{brief.notes.join(" · ")}</p> : null}
      </Section>
      <details className="card">
        <summary>How it investigated ({brief.steps.length} steps)</summary>
        <ol className="small">
          {brief.steps.map((s, i) => (
            <li key={`${String(i)}-${s.tool}`}>
              <span className={s.by === "code" ? "badge" : "badge warn-badge"}>{s.by}</span>{" "}
              {stepLine(s)}
              {s.evidence_ids.length ? (
                <span className="muted"> → {s.evidence_ids.join(", ")}</span>
              ) : null}
            </li>
          ))}
        </ol>
      </details>
      <details className="card" open={brief.evidence.length <= 4}>
        <summary>Sources ({brief.evidence.length})</summary>
        <p className="muted small">Checked with: {brief.checker}</p>
        <ul className="plain">
          {brief.evidence.map((e) => (
            <Evidence key={e.id} item={e} />
          ))}
        </ul>
      </details>
    </>
  );
}

/** Runs only when asked: an investigation may call a model several times. */
export function IncidentBriefPanel({ id }: { id: number }) {
  const [question, setQuestion] = useState("");
  const brief = useMutation({
    mutationFn: (q: string | undefined) => api.investigate(id, q),
  });
  const trimmed = question.trim();
  function submit(e: SyntheticEvent) {
    e.preventDefault();
    if (trimmed.length >= 3) brief.mutate(trimmed);
  }
  return (
    <>
      <Section
        title="Brief"
        hint="Code picks the culprit and gathers the evidence. The model may look further with read-only tools, and only words what the evidence says."
      >
        <div className="row">
          <button
            className="primary"
            disabled={brief.isPending}
            onClick={() => {
              brief.mutate(undefined);
            }}
          >
            {brief.isPending ? "Investigating…" : "Investigate this incident"}
          </button>
        </div>
        <form onSubmit={submit} className="row" style={{ marginTop: 12 }}>
          <input
            value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
            }}
            placeholder="Follow-up, e.g. why did the candidate change touch the payment code?"
            aria-label="Follow-up question"
          />
          <button type="submit" disabled={trimmed.length < 3 || brief.isPending}>
            Ask
          </button>
        </form>
      </Section>
      {brief.isError ? <ErrorBox error={brief.error} /> : null}
      {brief.data ? <Result brief={brief.data} /> : null}
    </>
  );
}
