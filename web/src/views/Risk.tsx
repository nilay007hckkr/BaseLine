import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "../api/client";
import { useApp } from "../state";
import { ErrorBox, Loading, PageHead, Section, fmtDate, pct } from "../ui";

export function Risk() {
  const { unlock } = useApp();
  const q = useQuery({ queryKey: ["risk"], queryFn: api.risk });
  useEffect(() => {
    if (q.isSuccess) unlock("risk");
  }, [q.isSuccess, unlock]);
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} />;
  const { files, components, min_changes, inactive_after_days } = q.data;
  const orphaned = files.filter((f) => !f.top_author_active).length;
  return (
    <>
      <PageHead
        label="Bus factor"
        title={`${String(files.length)} files hang on one person.`}
        lede={`${String(orphaned)} of them on someone quiet for ${String(Math.round(inactive_after_days / 30))}+ months.`}
      />
      <Section
        title="Knowledge at risk"
        hint={`One person made 80%+ of ${String(min_changes)}+ changes. Inactive: ${String(inactive_after_days)} days quiet.`}
      >
        {files.length === 0 ? (
          <p className="muted">No file depends on a single person.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Component</th>
                <th className="num">Changes</th>
                <th>Main author</th>
                <th className="num">Share</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.path}>
                  <td>
                    <a href={f.url} target="_blank" rel="noreferrer">
                      {f.path}
                    </a>
                  </td>
                  <td>{f.component}</td>
                  <td className="num">{f.changes}</td>
                  <td>{f.top_author}</td>
                  <td className="num">{pct(f.top_share)}</td>
                  <td>
                    {f.single_owner ? (
                      <span className="badge warn-badge">only one person</span>
                    ) : null}{" "}
                    {f.top_author_active ? (
                      <span className="badge">active</span>
                    ) : (
                      <span className="badge warn-badge">
                        inactive since {fmtDate(f.top_author_last_active_ts)}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <Section
        title="Bus factor by component"
        hint="Fewest people behind half the changes. 1 is fragile."
      >
        <table>
          <thead>
            <tr>
              <th>Component</th>
              <th className="num">Bus factor</th>
              <th className="num">People</th>
              <th className="num">Changes</th>
              <th>Main author</th>
              <th className="num">Share</th>
            </tr>
          </thead>
          <tbody>
            {components.map((c) => (
              <tr key={c.component}>
                <td>{c.component}</td>
                <td className="num">{c.bus_factor}</td>
                <td className="num">{c.authors}</td>
                <td className="num">{c.changes}</td>
                <td>{c.top_author}</td>
                <td className="num">{pct(c.top_share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  );
}
