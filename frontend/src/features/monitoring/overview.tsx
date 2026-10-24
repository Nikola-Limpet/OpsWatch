import { useState } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { useSnapshot } from '../../api';
import {
  age,
  Avatar,
  Badge,
  Empty,
  incidentState,
  number,
  ServiceEvidence,
  timestamp,
} from '../../components';
import type { Service } from '../../types';

export function OverviewPage() {
  const { data } = useSnapshot();
  const [evidence, setEvidence] = useState<Service | null>(null);
  if (!data) return null;
  const samples = data.services
    .filter((service) => service.latency_ms != null)
    .sort((a, b) => (b.latency_ms || 0) - (a.latency_ms || 0))
    .slice(0, 6);
  const scale = Math.max(
    1,
    ...samples.map((service) => service.latency_ms || 0),
  );
  return (
    <>
      <div className="overview-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>
                Service health
                <span className="counter">{data.summary.total}</span>
              </h2>
              <p>Latest checks, with attention needed first.</p>
            </div>
            <a className="text-link" href="/projects">
              All services
              <ArrowUpRight size={14} />
            </a>
          </div>
          {data.services.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Service</th>
                    <th scope="col">Provider</th>
                    <th scope="col">Status</th>
                    <th scope="col">Response</th>
                  </tr>
                </thead>
                <tbody>
                  {data.services.slice(0, 6).map((service) => (
                    <tr key={service.id}>
                      <td>
                        <button
                          className="service-button"
                          aria-label={`View ${service.name} evidence`}
                          onClick={() => setEvidence(service)}
                        >
                          <Avatar service={service} />
                          <span>
                            <span className="service-name">{service.name}</span>
                            <span className="service-sub">
                              {service.environment}
                            </span>
                          </span>
                        </button>
                      </td>
                      <td className="provider">{service.provider}</td>
                      <td>
                        <Badge status={service.status} />
                      </td>
                      <td
                        className={`latency numeric ${service.latency_ms != null && service.latency_ms > service.threshold_ms ? 'slow' : ''}`}
                      >
                        {service.latency_ms != null ? (
                          <>
                            {number(service.latency_ms)}{' '}
                            <span className="inline-unit">ms</span>
                          </>
                        ) : (
                          <span className="muted">No sample</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No services found">
              Enable targets in your configuration to see recorded checks here.
            </Empty>
          )}
          <div className="panel-footer">
            <span className="dot healthy" />
            Checks run independently for each service
          </div>
        </section>
        <section className="panel recent-panel">
          <div className="panel-header">
            <div>
              <h2>Recent incidents</h2>
              <p>Latest recorded activity.</p>
            </div>
            <a className="text-link" href="/incidents">
              View all
              <ArrowUpRight size={14} />
            </a>
          </div>
          {data.incidents.slice(0, 4).map((incident) => (
            <a
              className="incident-card"
              key={incident.ref}
              href={`/incidents?ref=${encodeURIComponent(incident.ref)}`}
              aria-label={`View incident ${incident.ref}`}
            >
              <div className="incident-top">
                <Badge status={incidentState(incident)} />
                <time title={timestamp(incident.updated, data.timezone)}>
                  {age(incident.updated)}
                </time>
              </div>
              <h3>
                {data.services.find((service) => service.id === incident.target)
                  ?.name || incident.target}
              </h3>
              <p>{incident.title}</p>
              <div className="incident-bottom">
                <span className="mono">{incident.ref}</span>
                <span>
                  View evidence
                  <ArrowUpRight size={13} />
                </span>
              </div>
            </a>
          ))}
          {!data.incidents.length ? (
            <Empty title="No incidents recorded">
              Incidents will appear here as the monitor records them.
            </Empty>
          ) : null}
        </section>
      </div>
      <section className="panel response-panel">
        <div className="panel-header">
          <div>
            <h2>Response times</h2>
            <p>Latest HTTP measurement per service.</p>
          </div>
          <span className="quiet-label">CURRENT SAMPLES · MS</span>
        </div>
        {samples.length ? (
          <div className="response-grid">
            {samples.map((service) => (
              <div key={service.id}>
                <div className="response-label">
                  <span>{service.name}</span>
                  <strong>{number(service.latency_ms || 0)} ms</strong>
                </div>
                <progress
                  className={`response-track ${service.latency_ms != null && service.latency_ms > service.threshold_ms ? 'slow' : ''}`}
                  aria-label={`${service.name} response in milliseconds`}
                  value={service.latency_ms || 0}
                  max={scale}
                  aria-valuetext={`${number(service.latency_ms || 0)} milliseconds; threshold ${number(service.threshold_ms)} milliseconds`}
                >
                  {number(service.latency_ms || 0)} ms
                </progress>
              </div>
            ))}
          </div>
        ) : (
          <Empty title="No current HTTP samples">
            Response times appear after a reachable HTTP endpoint is checked.
          </Empty>
        )}
        <div className="panel-footer">
          Each bar shows one recorded sample. Thresholds come from your monitor
          configuration.
        </div>
      </section>
      {evidence ? (
        <ServiceEvidence
          service={evidence}
          snapshot={data}
          onClose={() => setEvidence(null)}
        />
      ) : null}
    </>
  );
}
