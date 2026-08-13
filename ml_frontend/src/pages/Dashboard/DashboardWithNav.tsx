// src/pages/Dashboard/DashboardWithNav.tsx
import { useNavigate } from "react-router-dom"
import Dashboard from "./Dashboard"

export const DashboardWithNav = () => {
  const navigate = useNavigate();

  const handleExperimentSelect = (experimentId: string) => {
    navigate(`/experiment/${experimentId}`);
  };

  return <Dashboard onExperimentSelect={handleExperimentSelect} />;
};
