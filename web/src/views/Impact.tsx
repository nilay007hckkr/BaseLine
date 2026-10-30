import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useApp } from "../state";
import { api, type ImpactItem } from "../api/client";
import { ErrorBox, Provenance, ScoreBar, Section, fmtDate } from "../ui";

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

export function Impact() {
  const commits = useQuery({ queryKey: ["commits"], queryFn: api.commits });
  const [commit, setCommit] = useState("");
  const { takePreset, unlock } = useApp();
  // a quest hands over the files to check when it opens this page
  const [preset] = useState(() => takePreset("impact"));
  const [files, setFiles] = useState((preset?.files ?? []).join("\n"));
  const mutation = useMutation({
    mutationFn: api.impact,
    onSuccess: () => {
      unlock("impact");
    },
  });
  const { mutate } = mutation;

  useEffect(() => {
    if (preset?.run && preset.files?.length) mutate({ files: preset.files, commit: null });
  }, [preset, mutate]);
  const report = mutation.data;

  return (
    <>
      <Section
        title="What will this change affect?"
        hint="Pick a past commit (only history before it is used) or list the files you plan to change."
      >
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
      </Section>
      {mutation.isError ? <ErrorBox error={mutation.error} /> : null}
      {report ? (
        <>
          <Section title={`Changed: ${report.changed_files.join(", ") || "nothing"}`}>
            {report.notes.length ? <p className="muted small">{report.notes.join(" · ")}</p> : null}
            <p className="muted small">
              Score = {report.weights.cochange} × changed-together + {report.weights.static} ×
              service calls + {report.weights.same_component} × same service +{" "}
              {report.weights.recency} × recency. Reasons come from the recorded history, not from a
              language model.
            </p>
          </Section>
          <div className="grid">
            <Section title="Services and components">
              <Items items={report.components} empty="None found." />
            </Section>
            <Section title="Files">
              <Items
                items={report.files}
                empty="No other files have changed together with these."
              />
            </Section>
            <Section title="Tests to re-check">
              <Items items={report.tests} empty="No related tests found in history." />
            </Section>
            <Section title="Docs that may need updating">
              <Items items={report.docs} empty="No docs mention the changed parts." />
            </Section>
            <Section title="People who know this code">
              <Items items={report.people} empty="No earlier authors." />
            </Section>
            <Section title="Runbooks for affected services">
              <Items items={report.runbooks} empty="No runbook covers these services yet." />
            </Section>
          </div>
        </>
      ) : null}
    </>
  );
}
