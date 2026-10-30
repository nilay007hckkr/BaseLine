import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorBox, Loading, Section, fmtDate, pct } from "../ui";

export function Risk() {
  const q = useQuery({ queryKey: ["risk"], queryFn: api.risk });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} />;
  const { files, components, min_changes, inactive_after_days } = q.data;
  const owners = new Set(files.map((f) => f.top_author));
  const gone = new Set(files.filter((f) => !f.top_author_active).map((f) => f.top_author));
  const orphaned = files.filter((f) => !f.top_author_active).length;
  return (
    <>
      <section className="hero compact">
        <div>
          <div className="eyebrow">Bus factor</div>
          <h2 className="hero-title">
            {files.length} files depend on one person; {orphaned} of them on someone no longer
            active.
          </h2>
          <p className="hero-text">
            {owners.size} people each hold most of the know-how for some files, and {gone.size} of
            them have not committed for over {Math.round(inactive_after_days / 30)} months. Those
            files are the first to document or pair on.
          </p>
        </div>
      </section>
      <Section
        title="Knowledge at risk"
        hint={`Files where one person made at least 80% of ${String(min_changes)}+ human changes. "Inactive" means no commits in the last ${String(inactive_after_days)} days of the project's history.`}
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
        hint="The fewest people who together made at least half of the changes. 1 means one person holds most of the knowledge."
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
