import { useState } from 'react';
import { useSnapshot } from '../../api';
import { Badge, Empty, number, timestamp } from '../../components';

export function QueriesPage() {
  const { data, isError } = useSnapshot();
  const [limit, setLimit] = useState(20);
  if (!data) return null;
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Database queries</h2>
          <p>
            Recorded interval averages. Viewing this page does not execute SQL.
          </p>
        </div>
      </div>
      {data.queries.slice(0, limit).map((query, index) => (
        <article
          className="query-card"
          key={`${query.target}-${query.key}-${query.checked}-${index}`}
        >
          <div className="query-heading">
            <strong>{query.name}</strong>
            <Badge
              status={
                isError || query.stale || data.monitor.status !== 'running'
                  ? 'stale'
                  : query.healthy
                    ? 'healthy'
                    : 'warning'
              }
            />
          </div>
          <div className="query-meta">
            <span>
              Interval average{' '}
              <strong>
                {typeof query.evidence.interval_mean_ms === 'number'
                  ? `${number(query.evidence.interval_mean_ms, 2)} ms`
                  : 'No sample'}
              </strong>
            </span>
            <span>
              Calls{' '}
              <strong>
                {number(
                  typeof query.evidence.interval_calls === 'number'
                    ? query.evidence.interval_calls
                    : 0,
                )}
              </strong>
            </span>
            <span>Recorded {timestamp(query.checked, data.timezone)}</span>
          </div>
          <details>
            <summary>
              View SQL · {query.name} ·{' '}
              {String(query.evidence.query || 'No SQL').slice(0, 60)}
            </summary>
            <pre>
              {typeof query.evidence.query === 'string'
                ? query.evidence.query
                : 'No SQL text recorded.'}
            </pre>
          </details>
        </article>
      ))}
      {!data.queries.length ? (
        <Empty title="No query samples recorded">
          Enabled database targets report interval averages once enough query
          calls have been observed.
        </Empty>
      ) : null}
      {data.queries.length > limit ? (
        <div className="load-more">
          <button
            className="button"
            onClick={() => setLimit((value) => value + 20)}
          >
            Show more queries
          </button>
          <span>
            {Math.min(limit, data.queries.length)} of {data.queries.length}{' '}
            recorded samples shown
          </span>
        </div>
      ) : null}
    </section>
  );
}
