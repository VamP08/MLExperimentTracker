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

  const handleEdit = () => {
    setIsEditing(true);
  };

  const handleSave = async () => {
    if (!editedDescription || editedDescription === description) {
      setIsEditing(false);
      return;
    }
    try {
      const res = await fetch(`/api/experiment/${experimentId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ description: editedDescription })
      });
      if (!res.ok) throw new Error("Failed to update description");

      if (onEdit) onEdit(editedDescription);
    } catch (err) {
      console.error(err);
      alert("Failed to update description");
    } finally {
      setIsEditing(false);
    }
  };

  const handleCancel = () => {
    setEditedDescription(description);
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
            <button className="save-button" onClick={handleSave}>Save</button>
            <button className="cancel-button" onClick={handleCancel}>Cancel</button>
          </div>
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
