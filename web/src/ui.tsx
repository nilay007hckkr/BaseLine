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
  return <p className="label">Loading…</p>;
}

/** A page's opening: a pixel label, a thin headline and one plain sentence. */
export function PageHead({
  label,
  title,
  lede,
}: {
  label: string;
  title: string;
  lede?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="label">{label}</div>
      <h1 className="page-title thin">{title}</h1>
      {lede ? <p className="page-lede">{lede}</p> : null}
    </header>
  );
}

/** Sub-pages as big thin words; the active one is white. */
export function SubTabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: readonly { key: T; label: string }[];
  value: T;
  onChange: (key: T) => void;
}) {
  return (
    <div className="subtabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={t.key === value}
          className={t.key === value ? "subtab active" : "subtab"}
          onClick={() => {
            onChange(t.key);
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
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

/** Every colour means one thing, everywhere. */
export const COLOURS: { cls: string; name: string; means: string }[] = [
  { cls: "c-confirmed", name: "Green", means: "Confirmed: stated, passed, ready" },
  { cls: "c-history", name: "Blue", means: "History: commits, pull requests, issues" },
  { cls: "c-learned", name: "Orange", means: "Learned: patterns and predictions" },
  { cls: "c-broken", name: "Red", means: "Runtime: errors and incidents" },
  { cls: "c-code", name: "White", means: "Code and docs: the project's files" },
  { cls: "c-rule", name: "Grey", means: "Simple rule, baseline or inactive" },
  { cls: "c-people", name: "Gold", means: "People: authors, owners, your progress" },
];

export function ColourKey({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "colour-key compact" : "cm-card cm-rows colour-key"}>
      {COLOURS.map((c) => (
        <div key={c.cls} className={compact ? "colour-chip" : "cm-row"}>
          <span className={`swatch ${c.cls}`} aria-hidden="true" />
          <span className={c.cls}>{c.name}</span>
          {compact ? null : <span className="muted">{c.means}</span>}
        </div>
      ))}
    </div>
  );
}
