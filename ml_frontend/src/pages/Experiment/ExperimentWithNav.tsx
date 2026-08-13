// src/pages/Experiment/ExperimentWithNav.tsx
import { useParams, useNavigate } from "react-router-dom"
import Experiment from "./Experiment"

export const ExperimentWithNav = () => {
  const { id } = useParams();
  const navigate = useNavigate();

  const handleRunSelect = (runId: string) => {
    navigate(`/runs/${runId}`);
  };

  return <Experiment experimentId={id!} onRunSelect={handleRunSelect} />;
};
