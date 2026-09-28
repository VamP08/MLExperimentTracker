import { useState } from "react";
import type { FormEvent } from "react";
import { FiCalendar, FiCheck, FiClock, FiCopy, FiEdit2, FiGitBranch, FiGitCommit, FiPlus, FiX } from "react-icons/fi";
import "./RunHeader.css";

export interface RunHeaderData {
  _id: string;
  name: string;
  description: string;
  status: string;
  state: string | null;
  tags: string[];
  duration: number;
  durationFormatted: string;
  createdAt: string | null;
}

export interface GitFacts {
  commit: string | null;
  branch: string | null;
  dirty: boolean;
}

interface RunHeaderProps {
  run: RunHeaderData;
  git: GitFacts | null;
  onSaveDescription: (text: string) => Promise<void>;
  onSaveTags: (tags: string[]) => Promise<void>;
}

const STATE_LABEL: Record<string, string> = {
  initialized: "Initialized",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  interrupted: "Interrupted",
};

function formatWhen(iso: string | null): { text: string; zone: string } | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const text = date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const minutes = -date.getTimezoneOffset();
  const sign = minutes >= 0 ? "+" : "-";
  const abs = Math.abs(minutes);
  const zone = `UTC${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
  return { text, zone };
}

/**
 * Run name, state and identifying facts. The title is the run's `notes` field, which is also
 * its description, so editing one renames the run.
 */
const RunHeader = ({ run, git, onSaveDescription, onSaveTags }: RunHeaderProps) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const when = formatWhen(run.createdAt);
  const stateKey = run.state && STATE_LABEL[run.state] ? run.state : run.status;
  const badgeClass = stateKey === "interrupted" ? "archived" : stateKey === "initialized" ? "running" : stateKey;
  const duration = run.duration > 0 && run.duration < 60 ? `${run.duration.toFixed(3)} s` : run.durationFormatted;

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(run._id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be refused; the id stays selectable.
    }
  };

  const saveTitle = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onSaveDescription(draft.trim());
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  const addTag = async (e: FormEvent) => {
    e.preventDefault();
    const tag = tagDraft.trim();
    if (tag && !run.tags.includes(tag)) await onSaveTags([...run.tags, tag]);
    setTagDraft("");
    setAdding(false);
  };

  return (
    <header className="page-head run-head">
      {editing ? (
        <form className="run-title-edit" onSubmit={saveTitle}>
          <label htmlFor="run-description" className="sr-only">
            Run description
          </label>
          <input
            id="run-description"
            className="input"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
            placeholder={`Run ${run._id}`}
          />
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save
          </button>
          <button type="button" className="btn" onClick={() => setEditing(false)}>
            Cancel
          </button>
          <p className="run-title-hint">The description is also the run's display name, so saving renames it.</p>
        </form>
      ) : (
        <div className="run-title">
          <h1>{run.name}</h1>
          <span className={`badge ${badgeClass}`}>{STATE_LABEL[stateKey] ?? stateKey}</span>
          <button
            type="button"
            className="btn btn-icon btn-ghost run-title-btn"
            aria-label="Edit description"
            title="Edit description"
            onClick={() => {
              setDraft(run.description);
              setEditing(true);
            }}
          >
            <FiEdit2 />
          </button>
        </div>
      )}

      <div className="facts">
        <span className="id-pill">
          <code>{run._id}</code>
          <button type="button" onClick={copyId} aria-label="Copy run id" title={copied ? "Copied" : "Copy"}>
            {copied ? <FiCheck /> : <FiCopy />}
          </button>
        </span>
        {when && (
          <span title={run.createdAt ?? undefined}>
            <FiCalendar />
            <b className="num">{when.text}</b> {when.zone}
          </span>
        )}
        <span>
          <FiClock />
          Duration <b className="num">{duration}</b>
        </span>
        {git?.commit && (
          <span>
            <FiGitCommit />
            <code className="fact-link">{git.commit.slice(0, 7)}</code>
          </span>
        )}
        {git?.branch && (
          <span>
            <FiGitBranch />
            <b>{git.branch}</b>
            {git.dirty && <> + patch</>}
          </span>
        )}
        <span className="run-tags">
          {run.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
              <button type="button" className="tag-x" aria-label={`Remove tag ${tag}`} onClick={() => onSaveTags(run.tags.filter((t) => t !== tag))}>
                <FiX />
              </button>
            </span>
          ))}
          {adding ? (
            <form onSubmit={addTag} className="tag-form">
              <input
                className="input tag-input"
                value={tagDraft}
                autoFocus
                aria-label="New tag"
                placeholder="tag"
                onChange={(e) => setTagDraft(e.target.value)}
                onBlur={() => !tagDraft && setAdding(false)}
                onKeyDown={(e) => e.key === "Escape" && setAdding(false)}
              />
            </form>
          ) : (
            <button type="button" className="tag-add" onClick={() => setAdding(true)}>
              <FiPlus /> Add tag
            </button>
          )}
        </span>
      </div>
    </header>
  );
};

export default RunHeader;
