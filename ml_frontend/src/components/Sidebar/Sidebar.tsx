import React, { useState } from "react"
import { useNavigate, useLocation } from "react-router-dom"
import { FiHome, FiMenu, FiSettings, FiBarChart2, FiLayers } from "react-icons/fi"
import { useLastVisited } from "../../lib/navigationMemory"
import styles from "./Sidebar.module.css"

type Page = "dashboard" | "experiment" | "runs" | "settings"

type MenuItemProps = {
  icon: React.ReactNode
  text: string
  isExpanded: boolean
  isActive: boolean
  onClick: () => void
}

const MenuItem = ({ icon, text, isExpanded, isActive, onClick }: MenuItemProps) => {
  return (
    <button
      className={`${styles.menuItem} ${isActive ? styles.active : ""}`}
      onClick={onClick}
      aria-current={isActive ? "page" : undefined}
    >
      <span className={styles.menuItemIcon}>{icon}</span>
      {isExpanded && <span className={styles.menuItemText}>{text}</span>}
    </button>
  )
}

const Sidebar = () => {
  const [isExpanded, setIsExpanded] = useState(true)
  const navigate = useNavigate()
  const location = useLocation()

  // Map route paths to page keys for active state. Still correct now that the
  // two entries below carry ids: `/experiment/<id>` and `/runs/<id>` share the
  // prefixes these tests match, and no other route starts with either.
  const getActivePage = (): Page => {
    if (location.pathname.startsWith("/experiment")) return "experiment"
    if (location.pathname.startsWith("/runs")) return "runs"
    if (location.pathname.startsWith("/settings")) return "settings"
    // Default to dashboard for root or anything else
    return "dashboard"
  }

  const activePage = getActivePage()

  // `/experiment` and `/runs` resolve server-side to the most recently active
  // experiment and the newest run, so they were never a way *back* to anything.
  // Point them at the last experiment and run that actually loaded; fall back to
  // the parameterless routes only when nothing has been visited yet.
  const lastExperimentId = useLastVisited("experiment")
  const lastRunId = useLastVisited("run")

  const experimentPath = lastExperimentId
    ? `/experiment/${encodeURIComponent(lastExperimentId)}`
    : "/experiment"
  const runsPath = lastRunId ? `/runs/${encodeURIComponent(lastRunId)}` : "/runs"

  return (
    <div className={`${styles.sidebar} ${isExpanded ? styles.expanded : styles.collapsed}`}>
      <div className={styles.sidebarContent}>
        {/* Logo and Toggle Button */}
        <div className={styles.logoContainer}>
          {isExpanded ? (
            <>
              <div className={styles.logoIcon}>ML</div>
              <span className={styles.logoText}>ML Tracker</span>
              <button
                onClick={() => setIsExpanded(!isExpanded)}
                className={styles.hamburgerToggle}
                aria-label="Toggle sidebar"
                title="Toggle sidebar"
              >
                <FiMenu className={styles.icon} />
              </button>
            </>
          ) : (
            <>
              <div className={styles.logoIcon}>ML</div>
              <button
                onClick={() => setIsExpanded(!isExpanded)}
                className={`${styles.hamburgerToggle} ${styles.collapsedToggle}`}
                aria-label="Toggle sidebar"
                title="Toggle sidebar"
              >
                <FiMenu className={styles.icon} />
              </button>
            </>
          )}
        </div>

        {/* Menu Items */}
        <nav className={styles.nav}>
          <MenuItem
            icon={<FiHome />}
            text="Dashboard"
            isExpanded={isExpanded}
            isActive={activePage === "dashboard"}
            onClick={() => navigate("/")}
          />
          <MenuItem
            icon={<FiLayers />}
            text="Experiments"
            isExpanded={isExpanded}
            isActive={activePage === "experiment"}
            onClick={() => navigate(experimentPath)}
          />
          <MenuItem
            icon={<FiBarChart2 />}
            text="Runs"
            isExpanded={isExpanded}
            isActive={activePage === "runs"}
            onClick={() => navigate(runsPath)}
          />
          <MenuItem
            icon={<FiSettings />}
            text="Settings"
            isExpanded={isExpanded}
            isActive={activePage === "settings"}
            onClick={() => navigate("/settings")}
          />
        </nav>

        {isExpanded && (
          <div className={styles.sidebarFooter}>
            <p>ML Tracker v1.0.0</p>
          </div>
        )}
      </div>
    </div>
  )
}

export default Sidebar
