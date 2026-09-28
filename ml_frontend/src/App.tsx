import { useEffect } from "react"
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate, useParams } from "react-router-dom"

import Sidebar from "./components/Sidebar/Sidebar"
import DemoBanner from "./components/DemoBanner/DemoBanner"
import NotFound from "./components/Dashboard/NotFound/NotFound"
import Dashboard from "./pages/Dashboard/Dashboard"
import Experiment from "./pages/Experiment/Experiment"
import Runs from "./pages/Runs/Runs"
import Settings from "./pages/Settings/Settings"
// false outside the demo build. Vite inlines it, so the banner is dropped from the normal bundle.
import { IS_DEMO } from "./lib/api"
import "./App.css"

// The main pane scrolls, not the window, so the browser won't reset it on navigation.
const ScrollReset = () => {
  const { pathname } = useLocation()
  useEffect(() => {
    document.getElementById("main")?.scrollTo(0, 0)
  }, [pathname])
  return null
}

const ExperimentWrapper = () => {
  const { experimentId } = useParams<{ experimentId: string }>()
  const navigate = useNavigate()
  return <Experiment experimentId={experimentId || null} onRunSelect={(runId: string) => navigate(`/runs/${runId}`)} />
}

const RunsWrapper = () => {
  const { runId } = useParams<{ runId: string }>()
  return <Runs runId={runId || null} />
}

function App() {
  return (
    // BASE_URL comes from `base` in vite.config.ts ("/" for both builds today).
    <Router basename={import.meta.env.BASE_URL}>
      <ScrollReset />
      <div className="app">
        {IS_DEMO && <DemoBanner />}
        {/* Rail stays fixed; only the main pane scrolls. */}
        <div className="shell">
          <Sidebar />
          <main className="main" id="main">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/experiment/:experimentId" element={<ExperimentWrapper />} />
              <Route path="/experiment" element={<ExperimentWrapper />} />
              <Route path="/runs/:runId" element={<RunsWrapper />} />
              <Route path="/runs" element={<RunsWrapper />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </main>
        </div>
      </div>
    </Router>
  )
}

export default App
