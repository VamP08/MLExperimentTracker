import React, { useEffect, useState } from 'react';
import './Description.css';

interface DescriptionProps {
  description: string;
  onEdit?: (newDescription: string) => void;
}

const Description: React.FC<DescriptionProps> = ({ description, onEdit }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editedDescription, setEditedDescription] = useState(description);

  // Keep editedDescription in sync with props if description updates from parent
  useEffect(() => {
    setEditedDescription(description);
  }, [description]);

  const handleEdit = () => {
    setIsEditing(true);
  };

  const handleSave = () => {
    if (onEdit) {
      onEdit(editedDescription);
    }
    setIsEditing(false);
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
          <p className="description-text">
            {description || <i className="placeholder">No description available.</i>}
          </p>
          <button className="edit-button" onClick={handleEdit}>Edit</button>
        </div>
      )}
    </div>
  );
};

export default Description;
