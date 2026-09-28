import { useSyncExternalStore } from "react";
import { apiFetch } from "./api";

/**
 * `GET /api/dashboard`, fetched once and shared.
 *
 * The sidebar's experiment tree and the top bar's archive count both read it, and so does
 * the dashboard page. One request serves all three; `refreshDashboard()` re-reads it after a
 * write that changes what it reports (a renamed run, an edited description).
 */
export interface DashboardRun {
  _id: string;
  name: string;
  status: string;
  startedAt: string;
}

export interface DashboardExperiment {
  _id: string;
  name: string;
  description: string;
  tags: string[];
  runs: DashboardRun[];
  createdAt: string | null;
  stats: {
    totalRuns: number;
    completedRuns: number;
    failedRuns: number;
    runningRuns: number;
    successRate: string;
    avgDuration: string;
    lastRun: string;
  };
}

interface State {
  data: DashboardExperiment[] | null;
  error: string | null;
  loading: boolean;
}

let state: State = { data: null, error: null, loading: false };
let started = false;
const listeners = new Set<() => void>();

function emit(next: State) {
  state = next;
  listeners.forEach((listener) => listener());
}

export async function refreshDashboard(): Promise<void> {
  emit({ ...state, loading: true, error: null });
  try {
    const res = await apiFetch("/api/dashboard");
    if (!res.ok) throw new Error(`Request failed with ${res.status}`);
    const body = await res.json();
    emit({ data: Array.isArray(body?.data) ? body.data : [], error: null, loading: false });
  } catch (err) {
    emit({ data: state.data, error: err instanceof Error ? err.message : "Unknown error", loading: false });
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!started) {
    started = true;
    void refreshDashboard();
  }
  return () => listeners.delete(listener);
}

export function useDashboard(): State {
  return useSyncExternalStore(subscribe, () => state, () => state);
}
