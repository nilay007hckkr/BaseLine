import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorBox, Loading, PageHead, Provenance, Section, fmtDate, pct } from "../ui";

interface Stats {
  commits: number;
  commits_used_for_statistics: number;
  bot_commits: number;
  bot_share: number;
  sweep_commits: number;
  merge_commits: number;
  human_authors: number;
  first_commit_ts: number;
  last_commit_ts: number;
  nodes_by_kind: Record<string, number>;
  edges_by_kind_and_provenance: { kind: string; provenance: string; n: number }[];
  spans: number;
  flagged_spans: number;
}

interface Github {
  pages: Record<string, number>;
  complete: Record<string, boolean>;
  stopped: string | null;
  done: boolean;
}

export function Overview() {
  const q = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} />;
  const stats = q.data.stats as Stats | null;
  const repo = q.data.repo as { web?: string; head_sha?: string } | null;
  const github = q.data.github as Github | null;
  if (!stats) {
    return (
      <Section title="No project loaded yet">
        <p>
          Run <code>make ingest</code> to collect a repository's history.
        </p>
      </Section>
    );
  }
  const kinds = stats.nodes_by_kind;
  const tiles: [string, number | string, string][] = [
    [
      "Commits",
      stats.commits,
      `${fmtDate(stats.first_commit_ts)} → ${fmtDate(stats.last_commit_ts)}`,
    ],
    ["Counted", stats.commits_used_for_statistics, "bots, bulk and merges out"],
    ["Bots", pct(stats.bot_share), `${String(stats.bot_commits)} filtered`],
    ["People", stats.human_authors, "authors"],
    ["Pull requests", kinds.PR ?? 0, "linked"],
    ["Issues", kinds.Issue ?? 0, "tickets"],
    [
      "Discussions",
      (kinds.Comment ?? 0) + (kinds.Review ?? 0) || "—",
      (kinds.Comment ?? 0) + (kinds.Review ?? 0) ? "comments" : "next run (GitHub limit)",
    ],
    ["Doc sections", kinds.DocChunk ?? 0, `${String(kinds.Doc ?? 0)} files`],
  ];
  return (
    <>
      <PageHead
        label="The data"
        title="What Hindsight read."
        lede={
          repo?.web
            ? `${repo.web.replace("https://github.com/", "")} @ ${repo.head_sha?.slice(0, 7) ?? ""}`
            : undefined
        }
      />
      <div className="cm-label">History</div>
      <div className="cm-card cm-stats">
        {tiles.map(([label, value, note]) => (
          <div className="cm-stat" key={label}>
            <div className="cm-stat-label">{label}</div>
            <div className="cm-stat-value">{value}</div>
            <div className="cm-stat-sub">{note}</div>
          </div>
        ))}
      </div>
      <div className="cm-label">Links · colour = how it was found</div>
      <div className="cm-card cm-rows">
        {stats.edges_by_kind_and_provenance.map((e) => (
          <div className="cm-row" key={`${e.kind}-${e.provenance}`}>
            <span className={`prov-text-${e.provenance}`}>
              {e.kind.replaceAll("_", " ").toLowerCase()}
            </span>
            <Provenance value={e.provenance} />
            <span className="cm-row-value">{e.n.toLocaleString()}</span>
          </div>
        ))}
      </div>
      {github ? (
        <p className="small muted">
          GitHub: {github.pages.issues ?? 0} issue pages · {github.pages.pulls ?? 0} PR pages ·{" "}
          {(github.pages.issue_comments ?? 0) + (github.pages.review_comments ?? 0)} comment pages ·{" "}
          {github.done ? "complete" : "partial, next ingest continues"}
        </p>
      ) : null}
    </>
  );
}
