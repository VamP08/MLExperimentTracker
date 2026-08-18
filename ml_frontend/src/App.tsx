import { useEffect } from "react"
import { BrowserRouter as Router, Routes, Route, useNavigate, useParams } from "react-router-dom"

import Sidebar from "./components/Sidebar/Sidebar"
import DemoBanner from "./components/DemoBanner/DemoBanner"
import NotFound from "./components/Dashboard/NotFound/NotFound"
import { DashboardWithNav } from "./pages/Dashboard/DashboardWithNav"
import Experiment from "./pages/Experiment/Experiment"
import Runs from "./pages/Runs/Runs"
import Settings from "./pages/Settings/Settings"
// `false` in the normal build, and Vite substitutes the literal it is defined from, so the
// banner below folds away with it rather than shipping in the bundle the wheel packages.
import { IS_DEMO } from "./lib/api"
import "./App.css"

// Wrapper component to handle experiment route params and callbacks
const ExperimentWrapper = () => {
  const { experimentId } = useParams<{ experimentId: string }>()
  const navigate = useNavigate()

  // Pass onRunSelect callback to navigate to runs page
  const onRunSelect = (runId: string) => {
    navigate(`/runs/${runId}`)
  }

  return <Experiment experimentId={experimentId || null} onRunSelect={onRunSelect} />
}

// Wrapper component to handle runs route params
const RunsWrapper = () => {
  const { runId } = useParams<{ runId: string }>()
  return <Runs runId={runId || null} />
}

function App() {
  // Initialize theme from localStorage on app load
  useEffect(() => {
    const savedTheme = localStorage.getItem("theme") || "dark"
    document.documentElement.setAttribute("data-theme", savedTheme)
  }, [])

  return (
    /*
     * BASE_URL follows `base` in vite.config.ts: "/" for both the normal build, which
     * the Python server serves from its root, and the demo, which Render serves from its
     * own subdomain root. It is wired anyway so that a host publishing under a subpath
     * needs one constant changed and not a hunt through the router.
     */
    <Router basename={import.meta.env.BASE_URL}>
      <div className="app">
        {IS_DEMO && <DemoBanner />}
        <div className="app-container">
          <Sidebar />
          <main className="app-main">
            <Routes>
              <Route path="/" element={<DashboardWithNav />} />
              {/* Route with optional experimentId param */}
              <Route path="/experiment/:experimentId" element={<ExperimentWrapper />} />
              <Route path="/experiment" element={<ExperimentWrapper />} />
              {/* Route with optional runId param */}
              <Route path="/runs/:runId" element={<RunsWrapper />} />
              <Route path="/runs" element={<RunsWrapper />} />
              <Route path="/settings" element={<Settings />} />
              {/* Catch-all: an unmatched URL used to render the sidebar beside a blank area */}
              <Route path="*" element={<NotFound />} />
            </Routes>
          </main>
        </div>
      </div>
    </Router>
  )
}

export default App
