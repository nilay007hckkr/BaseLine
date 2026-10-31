import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";
import { api, type UploadReport } from "../api/client";
import { ErrorBox, Loading, PageHead, Provenance, Section, fmtDate } from "../ui";

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

/** Docs whose subject kept changing after their last edit, with the commits as evidence. */
function StaleDocs() {
  const stale = useQuery({ queryKey: ["docs-stale"], queryFn: api.staleDocs });
  if (stale.isPending) return <Loading />;
  if (stale.isError) return <ErrorBox error={stale.error} />;
  return (
    <Section
      title="May be out of date"
      hint="3 or more commits by people changed what the doc describes after its last edit. A signal, not a verdict."
    >
      {stale.data.length === 0 ? (
        <p className="muted">No doc looks out of date.</p>
      ) : (
        <ul className="plain">
          {stale.data.slice(0, 8).map((d) => (
            <li key={d.target}>
              <a href={d.url} target="_blank" rel="noreferrer">
                {d.target}
              </a>
              <span className="muted small"> · {d.reasons[0]?.text}</span>
              <div className="small">
                {d.reasons.slice(1).map((r) => (
                  <span key={r.text} style={{ marginRight: 12 }}>
                    <Provenance value={r.provenance} />{" "}
                    {r.url ? (
                      <a href={r.url} target="_blank" rel="noreferrer">
                        {r.text}
                      </a>
                    ) : (
                      r.text
                    )}
                  </span>
                ))}
              </div>
            </li>
          ))}
          {stale.data.length > 8 ? (
            <li className="muted small">and {stale.data.length - 8} more</li>
          ) : null}
        </ul>
      )}
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
      <PageHead
        label="Docs"
        title="Docs go stale. Hindsight says which."
        lede="Every doc shows its version and age; the ones whose code kept changing are listed first."
      />
      <StaleDocs />
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
