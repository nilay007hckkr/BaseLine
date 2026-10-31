import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Search } from "lucide-react";
import { useState } from "react";
import {
  api,
  type LlmProvider,
  type LlmSettings,
  type ModelList,
  type ProviderName,
} from "../api/client";
import { ErrorBox, Loading, PageHead } from "../ui";

/** What "Check" found, in plain words: ready, not pulled, or not running. */
function checkNote(p: LlmProvider, model: string, found: ModelList | undefined): string {
  if (!found) {
    return p.key_env
      ? `Key lives in .env as ${p.key_env}`
      : "Runs locally through Ollama; nothing leaves the machine";
  }
  if (found.error) {
    return p.key_env ? found.error : `${found.error}: start it with \`ollama serve\``;
  }
  const m = model.trim();
  const has = found.models.includes(m) || found.models.includes(`${m}:latest`);
  if (m && !has) {
    return p.key_env
      ? `${m} is not offered by ${p.label}; pick another below`
      : `${m} is not pulled: run \`ollama pull ${m}\``;
  }
  return m
    ? `Ready: ${m} is available (${String(found.models.length)} models)`
    : `${String(found.models.length)} models available · pick one above`;
}

interface Draft {
  order: ProviderName[];
  models: Partial<Record<ProviderName, string>>;
}

function Provider({
  p,
  index,
  on,
  active,
  model,
  setModel,
  move,
  toggle,
}: {
  p: LlmProvider;
  index: number;
  on: boolean;
  active: boolean;
  model: string;
  setModel: (m: string) => void;
  move: (delta: number) => void;
  toggle: () => void;
}) {
  const list = useQuery({
    queryKey: ["llm-models", p.name],
    queryFn: () => api.llmModels(p.name),
    enabled: false,
  });
  const options = list.data?.models ?? [];
  return (
    <div className={on ? "cm-card provider" : "cm-card provider off"}>
      <div className="provider-head">
        <span className="provider-rank">{on ? String(index + 1).padStart(2, "0") : "--"}</span>
        <span className="provider-name">{p.label}</span>
        {active ? <span className="badge c-confirmed">First choice</span> : null}
        {p.key_env ? (
          <span className={p.key_set ? "badge c-confirmed" : "badge c-rule"}>
            {p.key_set ? "Key set" : "No key"}
          </span>
        ) : (
          <span className="badge c-code">On this machine</span>
        )}
        <span className="provider-tools">
          {on ? (
            <>
              <button
                className="ghost"
                aria-label={`Move ${p.label} up`}
                onClick={() => {
                  move(-1);
                }}
              >
                <ArrowUp size={15} aria-hidden="true" />
              </button>
              <button
                className="ghost"
                aria-label={`Move ${p.label} down`}
                onClick={() => {
                  move(1);
                }}
              >
                <ArrowDown size={15} aria-hidden="true" />
              </button>
            </>
          ) : null}
          <button className="ghost" onClick={toggle}>
            {on ? "Turn off" : "Turn on"}
          </button>
        </span>
      </div>
      <div className="provider-model">
        <label className="label" htmlFor={`model-${p.name}`}>
          Model
        </label>
        <input
          id={`model-${p.name}`}
          list={`models-${p.name}`}
          value={model}
          placeholder={p.key_env && !p.key_set ? `add ${p.key_env} to .env first` : "model name"}
          onChange={(e) => {
            setModel(e.target.value);
          }}
        />
        <datalist id={`models-${p.name}`}>
          {options.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <button
          className="ghost"
          onClick={() => {
            void list.refetch();
          }}
          disabled={list.isFetching}
        >
          <Search size={14} aria-hidden="true" /> {list.isFetching ? "Checking…" : "Check"}
        </button>
      </div>
      <p className="small muted provider-note" role="status">
        {checkNote(p, model, list.data)}
      </p>
    </div>
  );
}

function Editor({ settings }: { settings: LlmSettings }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => ({
    order: settings.order,
    models: Object.fromEntries(settings.providers.map((p) => [p.name, p.model])),
  }));
  const save = useMutation({
    mutationFn: api.saveLlm,
    onSuccess: (next) => {
      client.setQueryData(["llm"], next);
    },
  });
  const byName = new Map(settings.providers.map((p) => [p.name, p]));
  const off = settings.providers.filter((p) => !draft.order.includes(p.name));
  const shown = [...draft.order.map((n) => byName.get(n)), ...off].filter(
    (p): p is LlmProvider => p !== undefined,
  );
  const saved = save.data ?? settings;

  function move(name: ProviderName, delta: number) {
    const order = [...draft.order];
    const i = order.indexOf(name);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j] ?? name, order[i] ?? name];
    setDraft({ ...draft, order });
  }

  function toggle(name: ProviderName) {
    const order = draft.order.includes(name)
      ? draft.order.filter((n) => n !== name)
      : [...draft.order, name];
    setDraft({ ...draft, order });
  }

  return (
    <>
      {!settings.allow_external ? (
        <p className="warn small">Cloud models are off (LLM_ALLOW_EXTERNAL=false in .env).</p>
      ) : null}
      <div className="label cm-label">Tried in this order</div>
      {shown.map((p) => {
        const on = draft.order.includes(p.name);
        return (
          <Provider
            key={p.name}
            p={p}
            index={draft.order.indexOf(p.name)}
            on={on}
            active={saved.active === p.name}
            model={draft.models[p.name] ?? ""}
            setModel={(m) => {
              setDraft({ ...draft, models: { ...draft.models, [p.name]: m } });
            }}
            move={(delta) => {
              move(p.name, delta);
            }}
            toggle={() => {
              toggle(p.name);
            }}
          />
        );
      })}
      {save.error ? <ErrorBox error={save.error} /> : null}
      <button
        className="bar-cta"
        onClick={() => {
          save.mutate({
            order: draft.order,
            models: Object.fromEntries(Object.entries(draft.models).filter(([, m]) => m.trim())),
          });
        }}
        disabled={save.isPending}
      >
        {save.isPending ? "Saving…" : save.isSuccess ? "Saved" : "Save"}
      </button>
    </>
  );
}

export function Settings() {
  const q = useQuery({ queryKey: ["llm"], queryFn: api.llm });
  return (
    <>
      <PageHead
        label="Settings"
        title="Models."
        lede="The model only words the answer. Code picks the evidence."
      />
      {q.isPending ? <Loading /> : null}
      {q.isError ? <ErrorBox error={q.error} /> : null}
      {q.data ? <Editor settings={q.data} /> : null}
    </>
  );
}
