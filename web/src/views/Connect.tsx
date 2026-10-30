import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useState, type SyntheticEvent } from "react";
import { api, type WorkspaceStatus } from "../api/client";
import { useInstance } from "../instance";
import { useApp } from "../state";

function Brand() {
  return (
    <div className="brand runner-brand">
      <span className="brand-mark" aria-hidden="true">
        <span>H</span>
      </span>
      HINDSIGHT
    </div>
  );
}

function repoName(url: string | null | undefined): string {
  return (url ?? "").replace(/^https:\/\/github\.com\//, "");
}

/** Workspace, first screen: one field for a GitHub repository. */
export function Connect({ status, onBack }: { status?: WorkspaceStatus; onBack?: () => void }) {
  const { demoUrl } = useInstance();
  const [url, setUrl] = useState("");
  const client = useQueryClient();
  const connect = useMutation({
    mutationFn: api.connect,
    onSuccess: (next) => {
      client.setQueryData(["workspace"], next);
      onBack?.();
    },
  });
  const failed = status?.job === "failed" ? status.error : null;
  const error = connect.error ? connect.error.message.replace(/^\d+ [^:]*: /, "") : failed;

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (url.trim()) connect.mutate(url.trim());
  }

  return (
    <form className="connect" onSubmit={submit}>
      <header className="connect-top">
        <Brand />
        {onBack ? (
          <button type="button" className="ghost" onClick={onBack}>
            <ArrowLeft size={16} aria-hidden="true" /> Back
          </button>
        ) : (
          <a className="pixel connect-demo" href={demoUrl}>
            Open the demo <ArrowRight size={14} aria-hidden="true" />
          </a>
        )}
      </header>
      <div className="connect-body">
        <label className="connect-label" htmlFor="repo-url">
          Enter your repo
        </label>
        <input
          id="repo-url"
          className="connect-input"
          placeholder="https://github.com/owner/name"
          value={url}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setUrl(e.target.value);
          }}
        />
        {error ? (
          <p className="connect-error" role="alert">
            {error}
          </p>
        ) : null}
        <p className="connect-hint">
          Public GitHub repos · big ones go faster with GITHUB_TOKEN in .env
        </p>
      </div>
      <button
        type="submit"
        className="bar-cta connect-go"
        disabled={connect.isPending || !url.trim()}
      >
        {connect.isPending ? "Connecting…" : "Connect"}
      </button>
    </form>
  );
}

/** Workspace, while the repository is read: one big number and the current step. */
export function Connecting({ status }: { status: WorkspaceStatus }) {
  const { setOnboarded } = useApp();
  const pct = Math.round(status.progress * 100);
  return (
    <div className="connect">
      <header className="connect-top">
        <Brand />
        <button
          type="button"
          className="ghost"
          onClick={() => {
            setOnboarded(false);
          }}
        >
          Play the tour while you wait
        </button>
      </header>
      <div
        className="pct-card"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span className="pct-num">{pct}</span>
        <span className="pct-sign">%</span>
      </div>
      <div className="connect-copy">
        <div className="run-title">{repoName(status.job_repo)}</div>
        <p className="run-label">{status.stage}…</p>
      </div>
    </div>
  );
}
