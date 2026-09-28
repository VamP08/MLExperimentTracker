import { useNavigate } from "react-router-dom";
import Dashboard from "./Dashboard";

export const DashboardWithNav = () => {
  const navigate = useNavigate();
  return <Dashboard onExperimentSelect={(id) => navigate(`/experiment/${encodeURIComponent(id)}`)} />;
};
