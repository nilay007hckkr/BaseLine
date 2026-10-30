import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { api, type UploadReport } from "../api/client";
import { ErrorBox, Loading, Section, fmtDate } from "../ui";

function Report({ report }: { report: UploadReport }) {
  const status = {
    created: `Added as version ${String(report.version)}`,
    unchanged: "Identical to the latest version: nothing changed",
    needs_attention: "Stored, but no text could be read",
  }[report.status];
  return (
    <Section title={`${report.filename}: ${status}`}>
      <p>
        {report.chunks} sections · {report.mentions} links to services and files ·{" "}
        {report.runbook_links} runbook links
      </p>
      {report.flagged_sections.length ? (
        <p className="warn small">
          Flagged (instruction-like text, never followed or sent to a model):{" "}
          {report.flagged_sections.join(", ")}
        </p>
      ) : null}
      {report.warnings.map((w) => (
        <p key={w} className="muted small">
          {w}
        </p>
      ))}
    </Section>
  );
}

export function Docs() {
  const client = useQueryClient();
  const docs = useQuery({ queryKey: ["docs"], queryFn: api.docs });
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("runbook");
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadDoc(f, docType),
    onSuccess: () => client.invalidateQueries({ queryKey: ["docs"] }),
  });

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (file) upload.mutate(file);
  }

  return (
    <>
      <Section
        title="Add a document"
        hint="PDF or Markdown, linked to the services it names. Stays on this machine."
      >
        <form onSubmit={submit} className="row">
          <input
            type="file"
            accept=".md,.markdown,.pdf"
            aria-label="Document"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
            }}
          />
          <select
            value={docType}
            onChange={(e) => {
              setDocType(e.target.value);
            }}
            aria-label="Document type"
            className="narrow"
          >
            <option value="runbook">Runbook</option>
            <option value="design">Design</option>
            <option value="spec">Spec</option>
            <option value="other">Other</option>
          </select>
          <button type="submit" disabled={!file || upload.isPending}>
            {upload.isPending ? "Reading…" : "Upload"}
          </button>
        </form>
      </Section>
      {upload.isError ? <ErrorBox error={upload.error} /> : null}
      {upload.data ? <Report report={upload.data} /> : null}
      <Section title="Documents" hint="Each shows its version and age.">
        {docs.isPending ? <Loading /> : null}
        {docs.isError ? <ErrorBox error={docs.error} /> : null}
        {docs.data ? (
          <table>
            <thead>
              <tr>
                <th>Document</th>
                <th>Source</th>
                <th>Type</th>
                <th>Updated</th>
                <th className="num">Sections</th>
                <th className="num">Links</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {docs.data.map((d) => (
                <tr key={d.key} className={d.superseded ? "muted" : ""}>
                  <td>
                    <a href={d.url} target="_blank" rel="noreferrer">
                      {d.title}
                    </a>
                  </td>
                  <td>{d.source === "upload" ? "uploaded" : "repository"}</td>
                  <td>{d.doc_type}</td>
                  <td>{fmtDate(d.updated_ts)}</td>
                  <td className="num">{d.chunks}</td>
                  <td className="num">{d.mentions + d.runbook_links}</td>
                  <td>
                    {d.superseded ? <span className="badge">older version</span> : null}
                    {d.needs_attention ? (
                      <span className="badge warn-badge">needs attention</span>
                    ) : null}
                    {d.flagged ? (
                      <span className="badge warn-badge">{d.flagged} flagged</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Section>
    </>
  );
}
