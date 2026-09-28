import { useEffect } from "react"
import { Link, NavLink, useLocation } from "react-router-dom"
import { LuFlaskConical, LuLayoutGrid, LuList, LuSettings, LuX } from "react-icons/lu"
import { useLastVisited } from "../../lib/navigationMemory"
import { useDashboard } from "../../lib/dashboard"
import { setNavOpen, useNavOpen } from "../../lib/shell"
import { IS_DEMO } from "../../lib/api"
import styles from "./Sidebar.module.css"

// Interrupted runs are hollow; anything unrecognised reads as running, as it does everywhere else.
const statusClass = (status: string) =>
  status === "completed"
    ? styles.ok
    : status === "failed"
      ? styles.fail
      : status === "interrupted" || status === "archived"
        ? styles.idle
        : styles.live

/**
 * The fixed navigation rail: the four sections, then the archive itself as a tree.
 *
 * The tree answers "where am I and what is next to it" without a second request — the
 * dashboard payload already lists every experiment's runs. Only the experiment on screen is
 * expanded, so a large archive stays a list of names rather than a wall of runs.
 */
const Sidebar = () => {
  const location = useLocation()
  const open = useNavOpen()
  const { data } = useDashboard()

  // `/experiment` and `/runs` resolve server-side to the most recent experiment and run, so
  // they were never a way back. Point the entries at what last actually loaded.
  const lastExperimentId = useLastVisited("experiment")
  const lastRunId = useLastVisited("run")
  const experimentPath = lastExperimentId ? `/experiment/${encodeURIComponent(lastExperimentId)}` : "/experiment"
  const runsPath = lastRunId ? `/runs/${encodeURIComponent(lastRunId)}` : "/runs"

  const path = decodeURIComponent(location.pathname)
  const routeRunId = path.startsWith("/runs/") ? path.slice("/runs/".length) : null
  const routeExperimentId = path.startsWith("/experiment/") ? path.slice("/experiment/".length) : null
  const openExperimentId =
    routeExperimentId ??
    (routeRunId ? data?.find((e) => e.runs.some((r) => r._id === routeRunId))?._id : undefined) ??
    (path === "/experiment" ? lastExperimentId : null) ??
    (path === "/runs" && lastRunId ? data?.find((e) => e.runs.some((r) => r._id === lastRunId))?._id : undefined)

  // A drawer left open across a navigation would cover the page the user just asked for.
  useEffect(() => {
    setNavOpen(false)
  }, [location.pathname])

  const section = path.startsWith("/experiment")
    ? "experiment"
    : path.startsWith("/runs")
      ? "runs"
      : path.startsWith("/settings")
        ? "settings"
        : "dashboard"

  const items = [
    { key: "dashboard", to: "/", label: "Dashboard", icon: <LuLayoutGrid /> },
    { key: "experiment", to: experimentPath, label: "Experiments", icon: <LuFlaskConical /> },
    { key: "runs", to: runsPath, label: "Runs", icon: <LuList /> },
    { key: "settings", to: "/settings", label: "Settings", icon: <LuSettings /> },
  ]

  return (
    <>
      <button
        type="button"
        className={`${styles.backdrop} ${open ? styles.backdropOpen : ""}`}
        aria-label="Close navigation"
        tabIndex={open ? 0 : -1}
        onClick={() => setNavOpen(false)}
      />
      <aside className={`${styles.rail} ${open ? styles.open : ""}`} aria-label="Primary">
        <div className={styles.brand}>
          <span className={styles.mark} aria-hidden="true">
            <svg viewBox="0 0 16 16">
              <path d="M2 12.5 6 7.5l3 3 5-6.5" />
            </svg>
          </span>
          <Link to="/" className={styles.brandName}>
            MLExperimentTracker
          </Link>
          <button type="button" className={styles.close} aria-label="Close navigation" onClick={() => setNavOpen(false)}>
            <LuX />
          </button>
        </div>

        <nav className={styles.nav}>
          {items.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={styles.navItem}
              aria-current={section === item.key ? "page" : undefined}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
        </nav>

        {data && data.length > 0 && (
          <>
            <h2 className={styles.treeHead}>
              <span>Experiments</span>
              <span className="num">{data.length}</span>
            </h2>
            <ul className={styles.tree}>
              {data.map((experiment) => {
                const isOpen = experiment._id === openExperimentId
                return (
                  <li key={experiment._id}>
                    <NavLink
                      to={`/experiment/${encodeURIComponent(experiment._id)}`}
                      className={`${styles.treeItem} ${isOpen ? styles.treeOpen : ""}`}
                      title={experiment.name}
                    >
                      <LuFlaskConical />
                      <span className={styles.treeName}>{experiment.name}</span>
                      <span className={`${styles.treeCount} num`}>{experiment.stats.totalRuns}</span>
                    </NavLink>
                    {isOpen && experiment.runs.length > 0 && (
                      <ul className={styles.runs}>
                        {experiment.runs.map((run) => (
                          <li key={run._id}>
                            <Link
                              to={`/runs/${encodeURIComponent(run._id)}`}
                              className={styles.runItem}
                              aria-current={run._id === routeRunId ? "page" : undefined}
                              title={`${run.name} · ${run.status}`}
                            >
                              <span className={`${styles.dot} ${statusClass(run.status)}`} aria-hidden="true" />
                              <span className={styles.treeName}>{run.name}</span>
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                )
              })}
            </ul>
          </>
        )}

        {/* Where the data lives, rather than the package version: the version is only known to
            the server (/api/health), and the static demo has no server to ask. */}
        <div className={styles.foot}>
          <span>{IS_DEMO ? "Static demo" : "Local store"}</span>
          <b>{IS_DEMO ? "nothing is saved" : "your runs, on this machine"}</b>
        </div>
      </aside>
    </>
  )
}

export default Sidebar
