import { GLOSSARY } from "../glossary";
import { useApp, type ViewKey } from "../state";

interface Chapter {
  view: ViewKey;
  title: string;
  forWhom: string;
  how: string[];
  trust: string;
}

const CHAPTERS: Chapter[] = [
  {
    view: "ask",
    title: "Ask why",
    forWhom: "Anyone new to the code, or anyone about to change something old.",
    how: [
      "Type a question in plain English, e.g. “Why does the cart service use Redis?”.",
      "Optionally give a file and line numbers: the answer then comes from the history of exactly those lines.",
      "Each sentence ends with a source tag (S1, S2…). Click it, then open the commit, pull request or issue on GitHub.",
      "Rate the answer at the bottom: that is the one-minute check anyone can do.",
    ],
    trust:
      "Answers only quote the project's own history. If no source covers the question, or the matching text is about something else, the answer is “not found”.",
  },
  {
    view: "impact",
    title: "Impact",
    forWhom: "Developers and reviewers before a change; leads planning a release.",
    how: [
      "List the files you plan to change, or pick a past commit.",
      "You get the services, files, tests, docs, runbooks and people most likely involved, each with the reason it is listed.",
    ],
    trust:
      "Measured by replaying past commits using only earlier history, and compared with simple methods (see Accuracy).",
  },
  {
    view: "graph",
    title: "Map",
    forWhom: "Anyone who wants to see how code, changes, tickets and people connect.",
    how: [
      "Search for a service, file, pull request or person.",
      "Drag to rotate, scroll to zoom, click a dot to focus on it. The panel shows how each link was found.",
    ],
    trust: "Every line on the map stores its evidence; colours show how strong that evidence is.",
  },
  {
    view: "incidents",
    title: "Incidents",
    forWhom: "Whoever is on call when the shop misbehaves.",
    how: [
      "Hindsight watches the demo shop's logs and opens an incident on a new error pattern or an error spike.",
      "Each incident shows the service that broke, the services that only suffered, a timeline, and recent changes ranked as likely causes.",
    ],
    trust:
      "Measured on faults injected on purpose (labelled synthetic). A likely change is a correlation with its reasons, not proof.",
  },
  {
    view: "tests",
    title: "Tests",
    forWhom: "Testers and developers who are tired of tests breaking when a button is renamed.",
    how: [
      "Tests are written from tickets that cite a real issue, pull request or commit.",
      "Press Run. If a button changed its label or style, the test finds the same button and proposes a repair for you to approve.",
      "If something is really broken (a check fails, the server errors, the button is disabled), the test fails and is never healed.",
    ],
    trust: "Measured on deliberate page changes: target is zero false heals.",
  },
  {
    view: "risk",
    title: "Knowledge at risk",
    forWhom: "Leads and managers.",
    how: ["See files that only one person has worked on, and whether that person is still active."],
    trust: "Computed from commit history, ignoring bots and bulk changes.",
  },
  {
    view: "docs",
    title: "Docs",
    forWhom: "Anyone with runbooks or design notes outside the code.",
    how: [
      "Upload Markdown or PDF. Uploading the same title again creates a new version; the old one stops being used.",
      "Runbooks are linked to the services they mention and appear in impact reports and incidents.",
    ],
    trust: "Documents stay on this machine unless the operator allows otherwise.",
  },
];

export function Manual() {
  const { go } = useApp();
  return (
    <div className="manual">
      <section className="card">
        <h2>Start in one minute</h2>
        <ol>
          <li>Open Home and pick a quest, or press “Replay tour” for the guided adventure.</li>
          <li>Every page has an example: press it and read the result.</li>
          <li>Hover any word with a small “?” for a plain explanation.</li>
          <li>
            Unsure if something is true? Open its source link: everything is linked to GitHub.
          </li>
        </ol>
        <p className="small muted">
          Tour keys: → or Space to move on, ← to go back, Esc to skip to the dashboard.
        </p>
      </section>
      {CHAPTERS.map((c) => (
        <section key={c.view} className="card">
          <h2>{c.title}</h2>
          <p className="muted">For: {c.forWhom}</p>
          <ul>
            {c.how.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
          <p className="small">
            <strong>How far to trust it:</strong> {c.trust}
          </p>
          <button
            onClick={() => {
              go(c.view);
            }}
          >
            Open {c.title}
          </button>
        </section>
      ))}
      <section className="card">
        <h2>How the numbers are made</h2>
        <ul>
          <li>
            Every number on Accuracy and Home is read from a result file in the repository (
            <code>eval/results/</code>), with the command that reproduces it.
          </li>
          <li>
            Each is compared with a simple method that needs no Hindsight, and shown with its number
            of cases and a 95% range.
          </li>
          <li>
            Tests on past history use only what was known at that time, so the future cannot leak
            in.
          </li>
          <li>
            Anything made on purpose for testing (switched-off services, changed pages) is labelled
            synthetic.
          </li>
        </ul>
      </section>
      <section className="card">
        <h2>Glossary</h2>
        <dl className="glossary">
          {Object.entries(GLOSSARY).map(([id, g]) => (
            <div key={id}>
              <dt>{g.term}</dt>
              <dd>{g.plain}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
