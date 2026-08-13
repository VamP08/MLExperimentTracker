import React from 'react';
import './RunParams.css';

interface RunParamsProps {
  params: {
    name: string;
    value: string | number;
  }[];
}

const RunParams: React.FC<RunParamsProps> = ({ params }) => {
  return (
    <div className="run-params-overview">
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
