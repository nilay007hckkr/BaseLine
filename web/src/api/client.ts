import type { components } from "./schema";

type S = components["schemas"];
export type RepoStats = S["RepoStats"];
export type Answer = S["Answer"];
export type AskRequest = S["AskRequest"];
export type EvidenceItem = S["EvidenceItem"];
export type ImpactReport = S["ImpactReport"];
export type ImpactItem = S["ImpactItem"];
export type ImpactRequest = S["ImpactRequest"];
export type CommitSummary = S["CommitSummary"];
export type GraphResponse = S["GraphResponse"];
export type SearchHit = S["SearchHit"];
export type RiskReport = S["RiskReport"];
export type ScoreCard = S["ScoreCard"];
export type UploadReport = S["UploadReport"];
export type DocInfo = S["DocInfo"];
export type RunbookSection = S["RunbookSection"];
export type Incident = S["Incident"];
export type IncidentSummary = S["IncidentSummary"];
export type IncidentBrief = S["IncidentBrief"];
export type InvestigationStep = S["InvestigationStep"];
export type DeployRecord = S["DeployRecord"];
export type GraphStatus = S["GraphStatus"];
export type QaTest = S["QaTest"];
export type QaRepair = S["QaRepair"];
export type AnswerCheckRequest = S["AnswerCheckRequest"];
export type AnswerCheckSummary = S["AnswerCheckSummary"];
export type AppConfig = S["AppConfig"];
export type Feature = S["AppConfig"]["features"][number];
export type WorkspaceStatus = S["WorkspaceStatus"];
export type LlmSettings = S["LlmSettings"];
export type LlmProvider = S["LlmProvider"];
export type LlmSettingsUpdate = S["LlmSettingsUpdate"];
export type ProviderName = LlmProvider["name"];
export type ModelList = S["ModelList"];

/** Calls to one Hindsight API; `base` is "" for this server, or another instance's origin. */
export function makeApi(base = "") {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${base}/api${path}`, init);
    if (!res.ok) {
      throw new Error(`${String(res.status)} ${res.statusText}: ${await res.text()}`);
    }
    return (await res.json()) as T;
  }

  function send<T>(method: string, path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function post<T>(path: string, body: unknown): Promise<T> {
    return send<T>("POST", path, body);
  }

  return {
    config: () => request<AppConfig>("/config"),
    workspaceStatus: () => request<WorkspaceStatus>("/workspace/status"),
    connect: (url: string) => post<WorkspaceStatus>("/workspace/connect", { url }),
    llm: () => request<LlmSettings>("/settings/llm"),
    saveLlm: (body: LlmSettingsUpdate) => send<LlmSettings>("PUT", "/settings/llm", body),
    llmModels: (provider: ProviderName) => request<ModelList>(`/settings/llm/${provider}/models`),
    health: () => request<{ status: string }>("/health"),
    stats: () => request<RepoStats>("/repo/stats"),
    ask: (body: AskRequest) => post<Answer>("/ask", body),
    answerChecks: () => request<AnswerCheckSummary>("/answer-checks"),
    rateAnswer: (body: AnswerCheckRequest) => post<AnswerCheckSummary>("/answer-checks", body),
    impact: (body: ImpactRequest) => post<ImpactReport>("/impact", body),
    commits: () => request<CommitSummary[]>("/commits?limit=40"),
    /** An empty key lets the server pick where to start (the most linked service, component or file).
     * The services view draws every service and the calls between them. */
    graph: (key: string, depth: number, code = false, services = false) =>
      request<GraphResponse>(
        `/graph?${key ? `key=${encodeURIComponent(key)}&` : ""}depth=${String(depth)}${code ? "&code=true" : ""}${services ? "&view=services" : ""}`,
      ),
    graphStatus: () => request<GraphStatus>("/graph/status"),
    qaTests: () => request<QaTest[]>("/qa/tests"),
    runTest: (id: string) => post<QaTest>(`/qa/tests/${encodeURIComponent(id)}/run`, {}),
    qaRepairs: () => request<QaRepair[]>("/qa/repairs"),
    decideRepair: (id: number, decision: string) =>
      post<QaRepair>(`/qa/repairs/${String(id)}/${encodeURIComponent(decision)}`, {}),
    search: (q: string) => request<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
    risk: () => request<RiskReport>("/risk"),
    scorecard: () => request<ScoreCard[]>("/scorecard"),
    docs: () => request<DocInfo[]>("/docs"),
    uploadDoc: (file: File, docType: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("doc_type", docType);
      return request<UploadReport>("/docs", { method: "POST", body: form });
    },
    incidents: () => request<IncidentSummary[]>("/incidents"),
    incident: (id: number) => request<Incident>(`/incidents/${String(id)}`),
    investigate: (id: number, question?: string) =>
      post<IncidentBrief>(`/incidents/${String(id)}/investigate`, question ? { question } : {}),
    deploys: () => request<DeployRecord[]>("/deploys"),
    runbooks: (service: string) =>
      request<RunbookSection[]>(`/services/${encodeURIComponent(service)}/runbooks`),
  };
}

export type Api = ReturnType<typeof makeApi>;
export const api = makeApi();
