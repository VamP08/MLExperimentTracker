import React, { useEffect, useState } from 'react';
import './RunParams.css';

interface Param {
  name: string;
  value: string;
}

interface RunParamsProps {
  runId: string;
}

const RunParams: React.FC<RunParamsProps> = ({ runId }) => {
  const [params, setParams] = useState<Param[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchParams = async () => {
      try {
        setLoading(true);
        const response = await fetch(`/api/run/${runId}`);
        
        if (!response.ok) {
          throw new Error('Failed to fetch run parameters');
        }

        const data = await response.json();
        
        // Convert parameters object to array
        const paramsArray: Param[] = [];
        if (data.parameters) {
          Object.entries(data.parameters).forEach(([key, value]) => {
            paramsArray.push({
              name: key,
              value: String(value)
            });
          });
        }
        
        setParams(paramsArray);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Unknown error';
        setError(errorMessage);
      } finally {
        setLoading(false);
      }
    };

    if (runId) {
      fetchParams();
    }
  }, [runId]);

  if (loading) {
    return <div className="run-params-loading">Loading parameters...</div>;
  }

  if (error) {
    return <div className="run-params-error">Error: {error}</div>;
  }

  if (params.length === 0) {
    return <div className="run-params-empty">No parameters recorded for this run.</div>;
  }

  return (
    <div className="run-params-overview">
      <h2 className="params-title">Run Parameters</h2>
      <table className="params-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {params.map((param, index) => (
            <tr key={index}>
              <td className="param-name">{param.name}</td>
              <td className="param-value">{param.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default RunParams;
