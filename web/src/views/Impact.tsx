import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useApp } from "../state";
import { api, type ImpactItem, type ImpactReport, type ImpactRequest } from "../api/client";
import { ErrorBox, PageHead, Provenance, ScoreBar, SubTabs, fmtDate } from "../ui";

function Items({ items, empty }: { items: ImpactItem[]; empty: string }) {
  if (!items.length) return <p className="muted">{empty}</p>;
  return (
    <ul className="plain">
      {items.map((item) => (
        <li key={`${item.target_kind}-${item.target}`} className="impact-item">
          <div className="impact-head">
            <ScoreBar value={item.score} />
            {item.url ? (
              <a href={item.url} target="_blank" rel="noreferrer">
                {item.target}
              </a>
            ) : (
              <strong>{item.target}</strong>
            )}
          </div>
          <ul className="reasons">
            {item.reasons.map((r) => (
              <li key={r.text}>
                <Provenance value={r.provenance} />{" "}
                {r.url ? (
                  <a href={r.url} target="_blank" rel="noreferrer">
                    {r.text}
                  </a>
                ) : (
                  r.text
                )}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

type Tab = "components" | "files" | "tests" | "docs" | "people" | "runbooks";

function Report({ report }: { report: ImpactReport }) {
  const [tab, setTab] = useState<Tab>("components");
  const lists: Record<Tab, { label: string; items: ImpactItem[]; empty: string }> = {
    components: { label: "Services", items: report.components, empty: "None found." },
    files: {
      label: "Files",
      items: report.files,
      empty: "No other files have changed together with these.",
    },
    tests: { label: "Tests", items: report.tests, empty: "No related tests found in history." },
    docs: { label: "Docs", items: report.docs, empty: "No docs mention the changed parts." },
    people: { label: "People", items: report.people, empty: "No earlier authors." },
    runbooks: {
      label: "Runbooks",
      items: report.runbooks,
      empty: "No runbook covers these services yet.",
    },
  };
  const keys = Object.keys(lists) as Tab[];
  return (
    <>
      <div className="label">Changing: {report.changed_files.join(", ") || "nothing"}</div>
      {report.notes.length ? <p className="muted small">{report.notes.join(" · ")}</p> : null}
      <SubTabs
        tabs={keys.map((k) => ({
          key: k,
          label: `${lists[k].label} ${String(lists[k].items.length)}`,
        }))}
        value={tab}
        onChange={setTab}
      />
      <Items items={lists[tab].items} empty={lists[tab].empty} />
      <p className="muted small" style={{ marginTop: 24 }}>
        Score = {report.weights.cochange} × changed together + {report.weights.static} × service
        calls + {report.weights.same_component} × same service + {report.weights.recency} × recency.
        Every reason comes from recorded history, not from a language model.
      </p>
    </>
  );
}

export function Impact() {
  const commits = useQuery({ queryKey: ["commits"], queryFn: api.commits });
  const [commit, setCommit] = useState("");
  const { takePreset, unlock } = useApp();
  // a quest hands over the files to check when it opens this page
  const [preset] = useState(() => takePreset("impact"));
  const [files, setFiles] = useState((preset?.files ?? []).join("\n"));
  const [pr, setPr] = useState("");
  const [copied, setCopied] = useState("");
  // a past commit, a list of files, or a GitHub pull request (the PR check)
  const mutation = useMutation({
    mutationFn: (req: ImpactRequest | { pr: number }) =>
      "pr" in req ? api.impactPr(req.pr) : api.impact(req),
    onSuccess: () => {
      unlock("impact");
      setCopied("");
    },
  });
  const { mutate } = mutation;

  useEffect(() => {
    if (preset?.run && preset.files?.length) mutate({ files: preset.files, commit: null });
  }, [preset, mutate]);
  const report = mutation.data;
  const vars = mutation.variables;
  const prNumber = vars && "pr" in vars ? vars.pr : null;

  return (
    <>
      <PageHead
        label="Impact"
        title="What will this change affect?"
        lede="Files you will change, a past change, or an open pull request."
      />
      <div style={{ margin: "18px 0 30px" }}>
        <div className="row">
          <select
            value={commit}
            onChange={(e) => {
              setCommit(e.target.value);
            }}
            aria-label="Commit"
          >
            <option value="">Choose a recent commit…</option>
            {(commits.data ?? []).map((c) => (
              <option key={c.sha} value={c.sha}>
                {c.sha.slice(0, 7)} · {fmtDate(c.ts)} · {c.title.slice(0, 70)}
              </option>
            ))}
          </select>
          <button
            disabled={!commit || mutation.isPending}
            onClick={() => {
              mutation.mutate({ commit, files: [] });
            }}
          >
            Check commit
          </button>
        </div>
        <div className="row">
          <textarea
            value={files}
            onChange={(e) => {
              setFiles(e.target.value);
            }}
            placeholder={"Files you plan to change, one per line\nsrc/checkoutservice/main.go"}
            aria-label="Files"
            rows={3}
          />
          <button
            disabled={!files.trim() || mutation.isPending}
            onClick={() => {
              mutation.mutate({
                files: files
                  .split("\n")
                  .map((f) => f.trim())
                  .filter(Boolean),
                commit: null,
              });
            }}
          >
            Check files
          </button>
        </div>
        <div className="row">
          <input
            value={pr}
            onChange={(e) => {
              setPr(e.target.value.replace(/\D/g, ""));
            }}
            inputMode="numeric"
            placeholder="GitHub pull request number"
            aria-label="Pull request number"
          />
          <button
            disabled={!pr || mutation.isPending}
            onClick={() => {
              mutation.mutate({ pr: Number(pr) });
            }}
          >
            Check PR
          </button>
        </div>
      </div>
      {mutation.isError ? <ErrorBox error={mutation.error} /> : null}
      {report && prNumber !== null ? (
        <div className="row">
          <button
            className="ghost"
            onClick={() => {
              void api
                .impactPrComment(prNumber)
                .then((md) => navigator.clipboard.writeText(md))
                .then(() => {
                  setCopied("Copied. Paste it as a comment on the PR; nothing was posted.");
                })
                .catch((e: unknown) => {
                  setCopied(`Could not copy: ${e instanceof Error ? e.message : String(e)}`);
                });
            }}
          >
            Copy as PR comment
          </button>
          {copied ? <span className="muted small">{copied}</span> : null}
        </div>
      ) : null}
      {report ? <Report report={report} /> : null}
    </>
  );
}
