import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useApp } from "../state";
import { api, type QaRepair, type QaTest } from "../api/client";
import { ErrorBox, Loading, Section, fmtDate } from "../ui";

const OUTCOME: Record<string, string> = {
  passed: "passed",
  healed: "healed: a repair waits for review",
  failed: "failed: a real problem, not healed",
  needs_human: "needs a person: no confident repair",
  flaky: "flaky: passed on a re-run",
};

function locator(l: Record<string, string>): string {
  return l.role ? `${l.role} "${l.name ?? ""}"` : `css ${l.css ?? ""} (flagged)`;
}

function TestRow({ test }: { test: QaTest }) {
  const client = useQueryClient();
  const { unlock } = useApp();
  const [open, setOpen] = useState(false);
  const run = useMutation({
    mutationFn: () => api.runTest(test.id),
    onSuccess: async () => {
      unlock("test");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["qa-tests"] }),
        client.invalidateQueries({ queryKey: ["qa-repairs"] }),
      ]);
    },
  });
  const last = test.last_run;
  return (
    <>
      <tr>
        <td>
          <button
            className="link"
            onClick={() => {
              setOpen(!open);
            }}
          >
            {open ? "▾" : "▸"} {test.id}
          </button>
        </td>
        <td>{test.title}</td>
        <td>
          {test.source_url ? (
            <a href={test.source_url} target="_blank" rel="noreferrer">
              {test.source_kind}{" "}
              {test.source_key.replace("gh:", "#").replace("commit:", "").slice(0, 12)}
            </a>
          ) : (
            test.source_key
          )}
          <div className="muted small">{test.source_title.slice(0, 70)}</div>
        </td>
        <td>
          <span className="badge">{test.status}</span>
          {test.reason ? <div className="warn small">{test.reason.slice(0, 160)}</div> : null}
        </td>
        <td>
          {last ? (
            <>
              <span className={`badge outcome-${last.outcome}`}>{last.outcome}</span>{" "}
              <span className="muted small">{fmtDate(last.started_at)}</span>
              {last.reason ? <div className="small">{last.reason.slice(0, 200)}</div> : null}
            </>
          ) : (
            <span className="muted small">not run</span>
          )}
        </td>
        <td>
          <button
            disabled={test.status !== "active" || run.isPending}
            onClick={() => {
              run.mutate();
            }}
          >
            {run.isPending ? "Running…" : "Run"}
          </button>
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={6}>
            {run.isError ? <ErrorBox error={run.error} /> : null}
            <p className="small">Covers: {test.services.join(", ")}</p>
            <ol className="small">
              {test.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <p className="small">Where the quoted texts come from in the repository:</p>
            <ul className="small">
              {test.grounding.map((g) => (
                <li key={`${String(g.step)}-${g.text}`}>
                  “{g.text}”{" "}
                  {g.found ? (
                    <>
                      → <code>{`${g.file ?? ""}:${String(g.line ?? "")}`}</code>
                      {g.origin_commit ? (
                        <span className="muted">
                          {" "}
                          · introduced by {g.origin_commit.slice(0, 7)}
                        </span>
                      ) : null}
                    </>
                  ) : (
                    <span className="muted">
                      not in the repository as literal text (computed at runtime)
                    </span>
                  )}
                </li>
              ))}
            </ul>
            <details>
              <summary className="small">Generated test (assertions are never edited)</summary>
              <pre className="code">{test.code}</pre>
            </details>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Repair({ repair }: { repair: QaRepair }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: string) => api.decideRepair(repair.id, decision),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["qa-repairs"] }),
        client.invalidateQueries({ queryKey: ["qa-tests"] }),
      ]),
  });
  return (
    <Section title={`${repair.test_id}, step ${String(repair.step + 1)}: ${repair.line.trim()}`}>
      <p>
        <code>{locator(repair.old_locator)}</code> → <code>{locator(repair.new_locator)}</code>{" "}
        <span className="badge">{repair.status}</span>
      </p>
      {repair.score !== null ? (
        <p className="small">
          Similarity {repair.score.toFixed(2)} (next best {(repair.runner_up ?? 0).toFixed(2)});
          accepted because it is ≥ 0.75 and at least 0.15 above the next. Every original assertion
          passed after the heal.
        </p>
      ) : null}
      <table className="small">
        <tbody>
          {Object.entries(repair.breakdown).map(([feature, value]) => (
            <tr key={feature}>
              <td>{feature}</td>
              <td>{value.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="shots">
        {repair.before_shot ? (
          <figure>
            <img src={`/api/qa/shots/${repair.before_shot}`} alt="Page when the locator failed" />
            <figcaption className="small">When the locator failed</figcaption>
          </figure>
        ) : null}
        {repair.after_shot ? (
          <figure>
            <img src={`/api/qa/shots/${repair.after_shot}`} alt="Page after the healed run" />
            <figcaption className="small">After the healed run</figcaption>
          </figure>
        ) : null}
      </div>
      <pre className="code">{repair.patch}</pre>
      {decide.isError ? <ErrorBox error={decide.error} /> : null}
      <div className="row">
        {repair.status === "proposed" ? (
          <>
            <button
              onClick={() => {
                decide.mutate("approve");
              }}
            >
              Approve
            </button>
            <button
              onClick={() => {
                decide.mutate("reject");
              }}
            >
              Reject
            </button>
          </>
        ) : null}
        {repair.status === "approved" ? (
          <button
            onClick={() => {
              decide.mutate("revert");
            }}
          >
            Revert
          </button>
        ) : null}
      </div>
    </Section>
  );
}

function RunAll({ tests }: { tests: QaTest[] }) {
  const client = useQueryClient();
  const { unlock } = useApp();
  const active = tests.filter((t) => t.status === "active");
  const run = useMutation({
    mutationFn: async () => {
      for (const t of active) await api.runTest(t.id); // one browser at a time
    },
    onSuccess: async () => {
      unlock("test");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["qa-tests"] }),
        client.invalidateQueries({ queryKey: ["qa-repairs"] }),
      ]);
    },
  });
  const passed = tests.filter((t) => t.last_run?.outcome === "passed").length;
  const ran = tests.filter((t) => t.last_run).length;
  return (
    <div className="row">
      <button
        className="primary"
        disabled={!active.length || run.isPending}
        onClick={() => {
          run.mutate();
        }}
      >
        {run.isPending
          ? `Running ${String(active.length)} tests in a real browser…`
          : `Run all ${String(active.length)} tests`}
      </button>
      {ran ? (
        <span className="small muted">
          Last runs: {passed} of {ran} passed.
        </span>
      ) : null}
      {run.isError ? <ErrorBox error={run.error} /> : null}
    </div>
  );
}

export function Tests() {
  const tests = useQuery({ queryKey: ["qa-tests"], queryFn: api.qaTests });
  const repairs = useQuery({ queryKey: ["qa-repairs"], queryFn: api.qaRepairs });
  return (
    <>
      <Section
        title="Tests from tickets"
        hint="Each ticket cites the issue, pull request or commit it comes from in the target repository. Its Given/When/Then lines become a browser test, kept only if it passes 3 times out of 3 on the healthy shop. When a button's label or markup changes, the healer finds the same control; it refuses when something is really broken (an assertion fails, the server errors, the control is disabled, or an incident is open)."
      >
        {tests.isPending ? <Loading /> : null}
        {tests.isError ? <ErrorBox error={tests.error} /> : null}
        {tests.data?.length ? <RunAll tests={tests.data} /> : null}
        {tests.data && !tests.data.length ? (
          <p>
            No tests yet: run <code>make qa-generate qa-validate</code> with the shop running.
          </p>
        ) : null}
        {tests.data?.length ? (
          <table>
            <thead>
              <tr>
                <th>Test</th>
                <th>What it checks</th>
                <th>Source</th>
                <th>Status</th>
                <th>Last run</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tests.data.map((t) => (
                <TestRow key={t.id} test={t} />
              ))}
            </tbody>
          </table>
        ) : null}
        <p className="muted small">
          {Object.entries(OUTCOME)
            .map(([k, v]) => `${k}: ${v.split(": ")[1] ?? v}`)
            .join(" · ")}
        </p>
      </Section>
      {repairs.data?.length ? (
        repairs.data.map((r) => <Repair key={r.id} repair={r} />)
      ) : (
        <Section title="Repairs">
          <p className="muted">No repairs proposed yet.</p>
        </Section>
      )}
    </>
  );
}
