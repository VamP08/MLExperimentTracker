import { useEffect, useState } from "react";
import { FiSearch } from "react-icons/fi";
import "./RunParams.css";
import { apiFetch } from "../../../lib/api";

interface Param {
  name: string;
  value: string;
  type: string;
}

interface RunParamsProps {
  runId: string;
}

function describe(value: unknown): Pick<Param, "value" | "type"> {
  if (value === null || value === undefined) return { value: "null", type: "null" };
  if (Array.isArray(value)) return { value: JSON.stringify(value), type: "array" };
  if (typeof value === "object") return { value: JSON.stringify(value), type: "object" };
  return { value: String(value), type: typeof value };
}

/** Every parameter of the run, in the order the API returns them, filterable by name or value. */
const RunParams = ({ runId }: RunParamsProps) => {
  const [params, setParams] = useState<Param[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    setParams(null);
    setError(null);
    apiFetch(`/api/run/${runId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const data = await res.json();
        const entries = Object.entries((data.parameters ?? {}) as Record<string, unknown>);
        if (!cancelled) setParams(entries.map(([name, value]) => ({ name, ...describe(value) })));
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Unknown error"));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const needle = query.trim().toLowerCase();
  const shown = params?.filter((p) => !needle || p.name.toLowerCase().includes(needle) || p.value.toLowerCase().includes(needle));

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="rp-head">
        <div className="panel-head">
          <h2 id="rp-head">Parameters</h2>
          {params && (
            <span className="sub num">{needle && shown ? `${shown.length} of ${params.length}` : params.length}</span>
          )}
          <span className="spacer" />
          {params && params.length > 0 && (
            <label className="rp-filter">
              <FiSearch aria-hidden="true" />
              <span className="sr-only">Filter parameters</span>
              <input
                className="input"
                type="search"
                placeholder="Filter by name or value"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          )}
        </div>
        {error ? (
          <div className="state error">Could not load parameters: {error}</div>
        ) : params === null || shown === undefined ? (
          <div className="state">Loading parameters…</div>
        ) : params.length === 0 ? (
          <div className="state">
            <h3>No parameters recorded</h3>
            <p>
              Pass a <code>config</code> to <code>init()</code> to record this run&rsquo;s hyperparameters.
            </p>
          </div>
        ) : shown.length === 0 ? (
          <div className="state">No parameter matches &ldquo;{query.trim()}&rdquo;.</div>
        ) : (
          <div className="table-wrap">
            <table className="table rp-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Value</th>
                  <th>Type</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.name}>
                    <td>{p.name}</td>
                    <td className="mono rp-value">{p.value}</td>
                    <td className="muted">{p.type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default RunParams;
