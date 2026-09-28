import { useEffect, useState } from "react";
import { FiAlertTriangle, FiArrowRight, FiCheck, FiChevronDown, FiCopy, FiHelpCircle, FiShield, FiShieldOff } from "react-icons/fi";
import { apiFetch } from "../../../lib/api";
import "./ReproSummary.css";

export type CheckStatus = "ok" | "drift" | "unknown";

export interface VerifyCheck {
  name: string;
  status: CheckStatus;
  expected: unknown;
  actual: unknown;
  detail: string;
}

export interface VerifyReport {
  verdict: "reproducible" | "drifted" | "unverifiable";
  summary: { ok: number; drift: number; unknown: number };
  checks: VerifyCheck[];
}

interface ReproSummaryProps {
  runId: string;
  /** Bumped by "Verify again" to re-run the check. */
  refreshKey: number;
  onOpenProvenance: () => void;
  onReport?: (report: VerifyReport | null) => void;
}

const LABELS: Record<string, string> = {
  manifest: "Manifest",
  "git.repository": "Repository",
  "git.remote": "Remote",
  "git.commit": "Commit",
  "git.head": "HEAD",
  "git.worktree": "Worktree",
  "git.patch": "Patch",
  "python.version": "Python",
  "python.implementation": "Implementation",
  "platform.system": "OS",
  "platform.machine": "Machine",
  packages: "Packages",
};

const VERDICT_TEXT = {
  reproducible: "Reproducible",
  drifted: "Drifted",
  unverifiable: "Unverifiable",
} as const;

function groupOf(name: string): "source" | "env" | "data" {
  if (name.startsWith("git.")) return "source";
  if (name.startsWith("dataset:") || name === "manifest") return "data";
  return "env";
}

function labelOf(name: string): string {
  if (name.startsWith("dataset:")) return "Dataset";
  return LABELS[name] ?? name;
}

/** The recorded value, shortened so a hash, a path or a URL fits one row. */
function display(check: VerifyCheck): string {
  const raw = check.actual ?? check.expected;
  if (raw === null || raw === undefined || raw === "") return "not recorded";
  let text = typeof raw === "string" ? raw : JSON.stringify(raw);
  if (/^[0-9a-f]{40,64}$/i.test(text)) text = text.slice(0, 7);
  text = text.replace(/^https?:\/\//, "").replace(/\.git$/, "");
  if (check.name.startsWith("dataset:")) {
    const path = check.name.slice("dataset:".length);
    const base = path.split(/[\\/]/).pop();
    return `${text.slice(0, 7)} ${base ?? ""}`.trim();
  }
  if (/[\\/]/.test(text) && text.length > 38) {
    const parts = text.split(/[\\/]/);
    text = `${parts[0]}${text.includes("\\") ? "\\" : "/"}…${text.includes("\\") ? "\\" : "/"}${parts.slice(-2).join(text.includes("\\") ? "\\" : "/")}`;
  }
  return text;
}

/**
 * Expected → found for a drifted check, shortened only as far as the two still differ. Two
 * hashes can share their first seven characters, and "3c1a61d → 3c1a61d" would tell the reader
 * nothing changed; when even the full values do not fit, the check's own explanation does.
 */
function driftText(check: VerifyCheck) {
  const was = display({ ...check, actual: check.expected });
  const now = display(check);
  if (was !== now) {
    return (
      <>
        <s>{was}</s> → {now}
      </>
    );
  }
  const a = String(check.expected ?? "");
  const b = String(check.actual ?? "");
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  if (a && b && i < 24) {
    const n = Math.max(7, i + 4);
    return (
      <>
        <s>{a.slice(0, n)}</s> → {b.slice(0, n)}
      </>
    );
  }
  // The two recorded values match (e.g. the patch file is intact) and the drift is in the tree
  // itself. The verifier's detail ends with the plain answer after its last colon — "the
  // uncommitted work has changed" — which fits the row; the full sentence is the row tooltip.
  const plain = check.detail.includes(": ") ? check.detail.slice(check.detail.lastIndexOf(": ") + 2) : check.detail;
  return <span className="rc-prose">{plain.charAt(0).toUpperCase() + plain.slice(1)}</span>;
}

const StatusIcon = ({ status }: { status: CheckStatus }) =>
  status === "ok" ? (
    <span className="rs-mark rs-ok" aria-label="Passes">
      <FiCheck />
    </span>
  ) : status === "drift" ? (
    <span className="rs-mark rs-drift" aria-label="Drifted">
      <FiAlertTriangle />
    </span>
  ) : (
    <span className="rs-mark rs-unknown" aria-label="Unverifiable">
      <FiHelpCircle />
    </span>
  );

/**
 * The first fact about a run: does the world it recorded still hold?
 *
 * One summary row by default — verdict, tally and the command that re-runs the check — so the
 * run's metrics stay in the first screen. The per-check breakdown is one click away, and it
 * opens on its own whenever the answer is anything but reproducible: a problem is never
 * hidden behind a disclosure.
 */
const ReproSummary = ({ runId, refreshKey, onOpenProvenance, onReport }: ReproSummaryProps) => {
  const [report, setReport] = useState<VerifyReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "absent" | "error">("loading");
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    (async () => {
      try {
        const res = await apiFetch(`/api/run/${runId}/verify`);
        if (cancelled) return;
        if (res.status === 404) {
          setReport(null);
          setState("absent");
          onReport?.(null);
          return;
        }
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const body: VerifyReport = await res.json();
        if (cancelled) return;
        setReport(body);
        setOpen(body.verdict !== "reproducible");
        setState("ready");
        onReport?.(body);
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
    // onReport is a callback prop; re-running the verification on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, refreshKey]);

  const command = `mlexp verify ${runId}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be refused; the command stays selectable on screen.
    }
  };

  const groups = report
    ? (["source", "env", "data"] as const).map((key) => ({
        key,
        title: key === "source" ? "Source code" : key === "env" ? "Environment" : "Data",
        checks: report.checks.filter((c) => groupOf(c.name) === key),
      }))
    : [];

  return (
    <section className={`repro panel ${open ? "is-open" : ""}`} aria-labelledby="repro-title">
      <div className="repro-bar">
        <h2 id="repro-title">Reproducibility</h2>

        {state === "loading" && <span className="repro-note">Checking the recorded world…</span>}
        {state === "error" && <span className="repro-note">The verification could not be run.</span>}
        {state === "absent" && (
          <span className="repro-verdict v-unknown">
            <FiShieldOff /> Not recorded
            <span className="repro-note">This run has no provenance manifest, so there is nothing to verify.</span>
          </span>
        )}
        {state === "ready" && report && (
          <>
            <span className={`repro-verdict v-${report.verdict}`}>
              {report.verdict === "reproducible" ? <FiShield /> : report.verdict === "drifted" ? <FiAlertTriangle /> : <FiHelpCircle />}
              {VERDICT_TEXT[report.verdict]}
            </span>
            <span className="repro-tally num">
              <b>{report.summary.ok}</b> of {report.checks.length} checks pass
              {report.summary.drift > 0 && <>, {report.summary.drift} drifted</>}
              {report.summary.unknown > 0 && <>, {report.summary.unknown} unverifiable</>}
            </span>
            <span className="repro-segs" aria-hidden="true">
              {report.checks.map((c) => (
                <i key={c.name} className={`seg-${c.status}`} />
              ))}
            </span>
          </>
        )}

        <span className="repro-spacer" />
        <span className="repro-cmd">
          <code>
            <b>mlexp verify</b> {runId}
          </code>
          <button type="button" onClick={copy} aria-label="Copy verify command" title={copied ? "Copied" : "Copy"}>
            {copied ? <FiCheck /> : <FiCopy />}
          </button>
        </span>
        <button type="button" className="repro-link" onClick={onOpenProvenance}>
          Provenance <FiArrowRight />
        </button>
        {state === "ready" && (
          <button type="button" className="btn repro-toggle" aria-expanded={open} aria-controls="repro-groups" onClick={() => setOpen(!open)}>
            {open ? "Hide checks" : "Show checks"}
            <FiChevronDown className="repro-chev" />
          </button>
        )}
      </div>

      {state === "ready" && report && (
        <div className="repro-groups" id="repro-groups" hidden={!open}>
          {groups.map((group) => (
            <div className="repro-group" key={group.key}>
              <h3>
                {group.title}
                <span className="num">
                  {group.checks.filter((c) => c.status === "ok").length}/{group.checks.length}
                </span>
              </h3>
              {group.checks.length === 0 ? (
                <p className="repro-none">Nothing recorded.</p>
              ) : (
                <ul className="repro-checks">
                  {group.checks.map((check) => (
                    <li key={check.name} title={check.detail}>
                      <StatusIcon status={check.status} />
                      <span className="rc-label">{labelOf(check.name)}</span>
                      <span className="rc-value mono">
                        {check.status === "drift" && check.expected != null ? driftText(check) : display(check)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {group.key === "data" && (
                <ul className="repro-checks repro-key" aria-label="Status key">
                  <li>
                    <StatusIcon status="ok" />
                    <span className="rc-label">Passes</span>
                    <span className="rc-value">matches the recorded value</span>
                  </li>
                  <li>
                    <StatusIcon status="drift" />
                    <span className="rc-label">Drifted</span>
                    <span className="rc-value">differs from what was recorded</span>
                  </li>
                  <li>
                    <StatusIcon status="unknown" />
                    <span className="rc-label">Unverifiable</span>
                    <span className="rc-value">could not be checked; never a pass</span>
                  </li>
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
};

export default ReproSummary;
