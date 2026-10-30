import { useMutation } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { api, type Answer, type EvidenceItem } from "../api/client";
import { ErrorBox, Kind, Section, fmtDate } from "../ui";

const EXAMPLES = [
  "Why does checkoutservice convert currency to USD?",
  "Why does the cart service use Redis?",
  "Why was paymentservice rewritten in Rust?",
];

function Evidence({ item }: { item: EvidenceItem }) {
  return (
    <li className="evidence" id={`ev-${item.id}`}>
      <div className="evidence-head">
        <span className="cite">{item.id}</span>
        <Kind value={item.kind} />
        <a href={item.url} target="_blank" rel="noreferrer">
          {item.title}
        </a>
        <span className="muted small">{fmtDate(item.ts)}</span>
      </div>
      {item.flagged ? (
        <p className="warn small">
          Contains instruction-like text. Shown as evidence only, never followed.
        </p>
      ) : null}
      <p className="snippet">{item.snippet}</p>
      {item.path.length > 1 ? (
        <p className="muted small">How it was found: {item.path.join(" → ")}</p>
      ) : null}
    </li>
  );
}

function Result({ answer }: { answer: Answer }) {
  if (answer.status === "not_found") {
    return (
      <Section title="Not found in project history">
        <p>There isn't enough recorded evidence to answer this, so Hindsight won't guess.</p>
        {answer.notes.length ? (
          <ul className="muted small">
            {answer.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
        {answer.nearest.length ? (
          <>
            <h3>Closest items</h3>
            <ul className="plain">
              {answer.nearest.map((e) => (
                <Evidence key={e.id} item={e} />
              ))}
            </ul>
          </>
        ) : null}
      </Section>
    );
  }
  return (
    <>
      <Section
        title="Answer"
        hint={
          answer.mode === "llm"
            ? "Written by the language model from the sources below; every sentence passed the citation check."
            : "Quoted directly from the sources below."
        }
      >
        <ul className="claims">
          {answer.claims.map((c) => (
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
        {answer.anchors.length ? (
          <p className="muted small">Started from: {answer.anchors.join(", ")}</p>
        ) : null}
        {answer.notes.length ? <p className="muted small">{answer.notes.join(" · ")}</p> : null}
      </Section>
      <Section title="Sources" hint={`Checked with: ${answer.checker}`}>
        <ul className="plain">
          {answer.evidence.map((e) => (
            <Evidence key={e.id} item={e} />
          ))}
        </ul>
      </Section>
    </>
  );
}

export function Ask() {
  const [question, setQuestion] = useState("");
  const [file, setFile] = useState("");
  const [lines, setLines] = useState("");
  const mutation = useMutation({ mutationFn: api.ask });

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    const [start, end] = lines.split("-").map((x) => Number.parseInt(x, 10));
    mutation.mutate({
      question,
      file: file.trim() || null,
      line_start: start && !Number.isNaN(start) ? start : null,
      line_end: end && !Number.isNaN(end) ? end : null,
    });
  }

  return (
    <>
      <Section
        title="Why is the code like this?"
        hint="Answers come only from commits, pull requests, issues, discussions and docs, with every source linked."
      >
        <form onSubmit={submit} className="stack">
          <input
            value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
            }}
            placeholder="Ask a why question…"
            aria-label="Question"
          />
          <div className="row">
            <input
              value={file}
              onChange={(e) => {
                setFile(e.target.value);
              }}
              placeholder="Optional file, e.g. src/checkoutservice/main.go"
              aria-label="File"
            />
            <input
              value={lines}
              onChange={(e) => {
                setLines(e.target.value);
              }}
              placeholder="Lines, e.g. 120-140"
              aria-label="Lines"
              className="narrow"
            />
            <button type="submit" disabled={question.trim().length < 3 || mutation.isPending}>
              {mutation.isPending ? "Searching…" : "Ask"}
            </button>
          </div>
        </form>
        <p className="muted small">
          Try:{" "}
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              className="link"
              onClick={() => {
                setQuestion(ex);
              }}
            >
              {ex}
            </button>
          ))}
        </p>
      </Section>
      {mutation.isError ? <ErrorBox error={mutation.error} /> : null}
      {mutation.data ? <Result answer={mutation.data} /> : null}
    </>
  );
}
