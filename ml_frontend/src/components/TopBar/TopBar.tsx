import type { ReactNode } from "react";
import { FiMenu, FiMoon, FiSun } from "react-icons/fi";
import Breadcrumbs from "../Breadcrumbs/Breadcrumbs";
import type { Crumb } from "../Breadcrumbs/Breadcrumbs";
import { useDashboard } from "../../lib/dashboard";
import { setNavOpen } from "../../lib/shell";
import { toggleTheme, useTheme } from "../../lib/theme";
import "./TopBar.css";

interface TopBarProps {
  crumbs: Crumb[];
  /** Page-specific controls, placed before the theme toggle. */
  actions?: ReactNode;
}

/**
 * The bar pinned to the top of the main pane: where you are, how big the archive is, and the
 * controls that belong to the page. It stays put while the page scrolls under it.
 */
const TopBar = ({ crumbs, actions }: TopBarProps) => {
  const theme = useTheme();
  const { data } = useDashboard();
  const runs = data?.reduce((total, experiment) => total + experiment.stats.totalRuns, 0) ?? 0;

  return (
    <header className="topbar">
      <button
        type="button"
        className="btn btn-icon btn-ghost topbar-menu"
        aria-label="Open navigation"
        onClick={() => setNavOpen(true)}
      >
        <FiMenu />
      </button>
      <Breadcrumbs items={crumbs} />
      <span className="topbar-spacer" />
      {data && (
        <span className="topbar-meta num">
          {data.length} {data.length === 1 ? "experiment" : "experiments"} · {runs}{" "}
          {runs === 1 ? "run" : "runs"}
        </span>
      )}
      <button
        type="button"
        className="btn btn-icon"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        title={theme === "dark" ? "Light mode" : "Dark mode"}
      >
        {theme === "dark" ? <FiSun /> : <FiMoon />}
      </button>
      {actions}
    </header>
  );
};

export default TopBar;
