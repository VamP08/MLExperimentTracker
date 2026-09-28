import { useSyncExternalStore } from "react";
import { apiFetch } from "./api";

/**
 * `GET /api/dashboard`, fetched once and shared by the sidebar, top bar and dashboard page.
 * Call `refreshDashboard()` after a write that changes it.
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
