import type React from "react"
import "./Experiments.css"

interface ExperimentsProps {
  experiments: string[]
  onExperimentClick?: (index: number) => void
}

const Experiments: React.FC<ExperimentsProps> = ({ experiments, onExperimentClick }) => {
  return (
    <div className="experiments">
      <h2 className="experiments-title">Experiments</h2>
      <ul className="experiments-list">
        {experiments.map((exp, i) => (
          <li key={i} className="experiment-item" onClick={() => onExperimentClick && onExperimentClick(i)}>
            <span>{exp}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default Experiments
