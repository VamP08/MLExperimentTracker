import { useState } from "react";
import type { FormEvent } from "react";
import { FiEdit2 } from "react-icons/fi";
import "./Description.css";
import { apiFetch } from "../../../../lib/api";

interface DescriptionProps {
  description: string;
  experimentId: string;
  onEdit?: (newDescription: string) => void;
}

/** The experiment's description under its title, edited in place. */
const Description = ({ description, experimentId, onEdit }: DescriptionProps) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editedDescription, setEditedDescription] = useState(description);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleEdit = () => {
    setSaveError(null);
    setEditedDescription(description);
    setIsEditing(true);
  };

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (editedDescription === description) {
      setIsEditing(false);
      return;
    }

    setSaving(true);
    setSaveError(null);
    try {
      const res = await apiFetch(`/api/experiment/${experimentId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        // An empty string clears the stored description; the server then falls back to
        // the first run's notes.
        body: JSON.stringify({ description: editedDescription }),
      });
      if (!res.ok) throw new Error(`Request failed with ${res.status}`);

      if (onEdit) onEdit(editedDescription);
      setIsEditing(false);
    } catch (err) {
      // Show the error inline and keep the editor open so the typed text isn't lost.
      setSaveError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditedDescription(description);
    setSaveError(null);
    setIsEditing(false);
  };

  if (isEditing) {
    return (
      <form className="exp-desc-edit" onSubmit={handleSave}>
        <label htmlFor="experiment-description" className="sr-only">
          Experiment description
        </label>
        <textarea
          id="experiment-description"
          className="input exp-desc-input"
          value={editedDescription}
          autoFocus
          rows={3}
          onChange={(e) => setEditedDescription(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && !saving && handleCancel()}
        />
        <div className="exp-desc-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="btn" onClick={handleCancel} disabled={saving}>
            Cancel
          </button>
        </div>
        {saveError && (
          <p className="exp-desc-error" role="alert">
            Could not save the description: {saveError}
          </p>
        )}
      </form>
    );
  }

  return (
    <div className="exp-desc">
      <p className={description ? undefined : "muted"}>{description || "No description."}</p>
      <button
        type="button"
        className="btn btn-icon btn-ghost exp-desc-btn"
        aria-label="Edit description"
        title="Edit description"
        onClick={handleEdit}
      >
        <FiEdit2 />
      </button>
    </div>
  );
};

export default Description;
