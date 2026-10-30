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
export type DeployRecord = S["DeployRecord"];
export type GraphStatus = S["GraphStatus"];
export type QaTest = S["QaTest"];
export type QaRepair = S["QaRepair"];
export type AnswerCheckRequest = S["AnswerCheckRequest"];
export type AnswerCheckSummary = S["AnswerCheckSummary"];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    throw new Error(`${String(res.status)} ${res.statusText}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  stats: () => request<RepoStats>("/repo/stats"),
  ask: (body: AskRequest) => post<Answer>("/ask", body),
  answerChecks: () => request<AnswerCheckSummary>("/answer-checks"),
  rateAnswer: (body: AnswerCheckRequest) => post<AnswerCheckSummary>("/answer-checks", body),
  impact: (body: ImpactRequest) => post<ImpactReport>("/impact", body),
  commits: () => request<CommitSummary[]>("/commits?limit=40"),
  graph: (key: string, depth: number) =>
    request<GraphResponse>(`/graph?key=${encodeURIComponent(key)}&depth=${String(depth)}`),
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
  deploys: () => request<DeployRecord[]>("/deploys"),
  runbooks: (service: string) =>
    request<RunbookSection[]>(`/services/${encodeURIComponent(service)}/runbooks`),
};
