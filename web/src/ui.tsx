import type { ReactNode } from "react";
import { GLOSSARY } from "./glossary";

export const PROVENANCE_HELP: Record<string, string> = {
  explicit: "Stated directly in the data, e.g. a diff or a 'fixes #N'",
  api: "Confirmed by GitHub's API",
  static: "Read from the source code",
  statistical: "Computed from the project's history",
  runtime: "Observed while the system ran",
  heuristic: "Inferred by a simple rule, e.g. a '#N' mention",
};

export function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

export function pct(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

export function Provenance({ value }: { value: string }) {
  return (
    <span className={`badge prov-${value}`} title={PROVENANCE_HELP[value] ?? value}>
      {GLOSSARY[value]?.term ?? value}
    </span>
  );
}

export function Kind({ value }: { value: string }) {
  return <span className="badge kind">{value}</span>;
}

export function ScoreBar({ value }: { value: number }) {
  const width = Math.max(4, Math.min(100, Math.round(value * 100)));
  return (
    <span className="scorebar" title={value.toFixed(2)}>
      <span style={{ width: `${String(width)}%` }} />
    </span>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="error">{error instanceof Error ? error.message : "Something went wrong"}</div>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}

export function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {hint ? <p className="muted">{hint}</p> : null}
      {children}
    </section>
  );
}
