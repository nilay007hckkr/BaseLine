import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorBox, Loading, Provenance, Section, fmtDate, pct } from "../ui";

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
    [
      "Used for statistics",
      stats.commits_used_for_statistics,
      "after removing bot, bulk and merge commits",
    ],
    [
      "Bot commits",
      pct(stats.bot_share),
      `${String(stats.bot_commits)} automated updates, filtered out`,
    ],
    ["People", stats.human_authors, "human authors"],
    ["Pull requests", kinds.PR ?? 0, "linked from commits and GitHub"],
    ["Issues", kinds.Issue ?? 0, "tickets"],
    [
      "Discussions",
      (kinds.Comment ?? 0) + (kinds.Review ?? 0) || "not yet",
      (kinds.Comment ?? 0) + (kinds.Review ?? 0)
        ? "PR and issue comments"
        : "comments arrive over later runs (GitHub's hourly limit)",
    ],
    ["Doc sections", kinds.DocChunk ?? 0, `from ${String(kinds.Doc ?? 0)} Markdown files`],
  ];
  return (
    <>
      <Section
        title="Project history collected"
        hint={repo?.web ? `Source: ${repo.web} at ${repo.head_sha?.slice(0, 7) ?? ""}` : undefined}
      >
        <div className="tiles">
          {tiles.map(([label, value, note]) => (
            <div className="tile" key={label}>
              <div className="tile-value">{value}</div>
              <div className="tile-label">{label}</div>
              <div className="muted small">{note}</div>
            </div>
          ))}
        </div>
      </Section>
      <Section
        title="How items are connected"
        hint="Every link records how it was found, so you can judge how much to trust it."
      >
        <table>
          <thead>
            <tr>
              <th>Link</th>
              <th>Found by</th>
              <th className="num">Count</th>
            </tr>
          </thead>
          <tbody>
            {stats.edges_by_kind_and_provenance.map((e) => (
              <tr key={`${e.kind}-${e.provenance}`}>
                <td>{e.kind.replaceAll("_", " ").toLowerCase()}</td>
                <td>
                  <Provenance value={e.provenance} />
                </td>
                <td className="num">{e.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      {github ? (
        <Section title="GitHub data (read-only)">
          <p className="muted">
            Pages cached: issues {github.pages.issues ?? 0}, pull requests {github.pages.pulls ?? 0}
            , comments {github.pages.issue_comments ?? 0}, review comments{" "}
            {github.pages.review_comments ?? 0}.{" "}
            {github.done
              ? "Complete."
              : `Partial${github.stopped ? `: ${github.stopped}` : ""}. Run make ingest again later to continue.`}
          </p>
        </Section>
      ) : null}
    </>
  );
}
