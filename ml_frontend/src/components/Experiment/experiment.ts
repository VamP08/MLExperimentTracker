/** One entry of `runs` in `GET /api/experiment/:id`. `status` is the raw state, unmapped. */
export interface ExperimentRun {
  _id: string;
  name: string;
  status: string;
  startedAt: string | null;
}

/** The slice of `GET /api/experiment[/:id]` the experiment page reads. */
export interface ExperimentData {
  _id: string;
  name: string;
  description: string;
  tags: string[];
  runs: ExperimentRun[];
  // Hardcoded empty on the server (GAPS M14); read anyway because it is part of the contract.
  activityTimeline: { date?: string; event?: string }[];
  stats: {
    totalRuns?: number;
    completedRuns?: number;
    failedRuns?: number;
    runningRuns?: number;
    successRate?: string;
    avgDuration?: string;
    lastRun?: string;
  };
}

/**
 * Badge class and label for a run state. Two endpoints feed this: the experiment payload sends
 * the raw state (`interrupted`, `initialized`), the runs table sends the mapped one (`archived`).
 */
export function runBadge(status: string): { tone: string; label: string } {
  const label = status ? status.charAt(0).toUpperCase() + status.slice(1) : "Unknown";
  switch (status) {
    case "completed":
    case "failed":
    case "running":
    case "archived":
      return { tone: status, label };
    case "initialized":
      return { tone: "running", label };
    case "interrupted":
      return { tone: "archived", label };
    // Anything else — including the literal "unknown" the endpoint sends for a run with no
    // summary — reads as running, the same rule the run page and the dashboard apply. A run
    // that died without writing a terminal state looks alive; that is the format's known gap.
    default:
      return { tone: "running", label: "Running" };
  }
}

/** `Sep 28, 2026, 14:02` in the reader's zone, or null for a missing or unparseable value. */
export function formatWhen(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
