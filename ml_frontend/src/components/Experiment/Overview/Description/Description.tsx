import React, { useState } from 'react';
import './Description.css';

interface DescriptionProps {
  description: string;
  experimentId: string;
  onEdit?: (newDescription: string) => void;
}

const Description: React.FC<DescriptionProps> = ({ description, experimentId, onEdit }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editedDescription, setEditedDescription] = useState(description);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleEdit = () => {
    setSaveError(null);
    setEditedDescription(description);
    setIsEditing(true);
  };

  const handleSave = async () => {
    if (editedDescription === description) {
      setIsEditing(false);
      return;
    }

    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetch(`/api/experiment/${experimentId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json"
        },
        // An empty string is a legitimate value, not a no-op: the server drops
        // the stored description and the experiment falls back to the one
        // derived from its first run's notes (GAPS M3).
        body: JSON.stringify({ description: editedDescription })
      });
      if (!res.ok) throw new Error(`Request failed with ${res.status}`);

      if (onEdit) onEdit(editedDescription);
      setIsEditing(false);
    } catch (err) {
      // GAPS N20: this used to be an alert(), which blocks the page and cannot
      // be styled or read by the surrounding layout. The editor stays open so
      // the text the user typed is not lost.
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

  return (
    <div className="run-description">
      <h3 className="description-title">Description</h3>
      
      {isEditing ? (
        <div className="description-edit-container">
          <textarea
            className="description-textarea"
            value={editedDescription}
            onChange={(e) => setEditedDescription(e.target.value)}
            rows={4}
          />
          <div className="description-actions">
            <button className="save-button" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button className="cancel-button" onClick={handleCancel} disabled={saving}>Cancel</button>
          </div>
          {saveError && (
            <p className="experiment-description-error" role="alert">
              Could not save the description: {saveError}
            </p>
          )}
        </div>
      ) : (
        <div className="description-wrapper">
          <p className="description-text">{description}</p>
          <button className="edit-button" onClick={handleEdit}>Edit</button>
        </div>
      )}
    </div>
  );
};

export default Description;
