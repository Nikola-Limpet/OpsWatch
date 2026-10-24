import { useState } from "react";
import { ArrowUpRight, BookOpen } from "lucide-react";
import { Markdown } from "./components";
import type { PstackCatalog } from "./model";

function commitUrl(catalog: PstackCatalog) {
  try {
    const url = new URL(catalog.repository);
    const [owner, repository] = url.pathname.split("/").filter(Boolean);
    url.pathname = `/${owner}/${repository}/commit/${catalog.revision}`;
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return catalog.repository;
  }
}

export function PstackGuide({ catalog }: { catalog: PstackCatalog }) {
  const [selected, setSelected] = useState("investigation");
  const playbook =
    catalog.playbooks.find((item) => item.id === selected) ??
    catalog.playbooks[0];
  const adaptation = Array.isArray(catalog.adaptation)
    ? catalog.adaptation
    : [catalog.adaptation];
  return (
    <section
      className="panel studio-pstack"
      aria-label="Pstack workflow guidance"
    >
      <div className="studio-section-heading">
        <div className="studio-section-title">
          <h2>
            <BookOpen size={17} />
            Pstack workflow
          </h2>
          <span className="badge">{catalog.license} licensed source</span>
        </div>
        <p className="muted">
          Read-only playbooks and principles from the upstream plugin.
        </p>
        <a
          className="studio-inline-link"
          href={commitUrl(catalog)}
          target="_blank"
          rel="noreferrer noopener"
        >
          Source revision <code>{catalog.revision.slice(0, 10)}</code>
          <ArrowUpRight size={13} />
        </a>
      </div>
      <div className="studio-pstack-grid">
        <section className="studio-pstack-playbook">
          <label className="studio-field" htmlFor="workflow-playbook">
            <span>Inspect a playbook</span>
            <select
              id="workflow-playbook"
              value={playbook?.id ?? ""}
              onChange={(event) => setSelected(event.target.value)}
            >
              {catalog.playbooks.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          {playbook ? (
            <>
              <p className="studio-pstack-description">
                {playbook.description}
              </p>
              <ol className="studio-pstack-steps">
                {playbook.steps.map((step, index) => (
                  <li key={index}>
                    <Markdown>{step}</Markdown>
                  </li>
                ))}
              </ol>
              <a
                className="studio-inline-link"
                href={playbook.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                Read upstream playbook
                <ArrowUpRight size={13} />
              </a>
            </>
          ) : null}
        </section>
        <section className="studio-pstack-principles">
          <h3>Guiding principles</h3>
          <dl>
            {catalog.principles.map((principle) => (
              <div key={principle.id}>
                <dt>
                  <a
                    href={principle.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {principle.title}
                    <ArrowUpRight size={12} />
                  </a>
                </dt>
                <dd>{principle.summary}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
      <div className="studio-pstack-adaptation">
        <h3>How OpsWatch uses this guidance</h3>
        {adaptation.map((text, index) => (
          <p key={index}>{text}</p>
        ))}
      </div>
    </section>
  );
}
