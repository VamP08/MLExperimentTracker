import { useState } from 'react';
import './Settings.css';

const Settings = () => {
  const [experimentName, setExperimentName] = useState('My Experiment');
  const [experimentDescription, setExperimentDescription] = useState('This is a sample experiment description.');

  const handleSave = () => {
    console.log('Saving experiment details:', { experimentName, experimentDescription });
    // Here you would typically call an API to save the changes
  };

  const handleDelete = () => {
    if (window.confirm('Are you sure you want to delete this experiment? This action cannot be undone.')) {
      console.log('Deleting experiment');
      // Here you would typically call an API to delete the experiment
    }
  };

  return (
    <div className="settings-container">
      {/* Experiment Details Section */}
      <div className="settings-card">
        <h3>Experiment Details</h3>
        
        <div className="form-group">
          <label htmlFor="experiment-name">Name</label>
          <input
            id="experiment-name"
            type="text"
            value={experimentName}
            onChange={(e) => setExperimentName(e.target.value)}
            className="form-control"
          />
        </div>
        
        <div className="form-group">
          <label htmlFor="experiment-description">Description</label>
          <textarea
            id="experiment-description"
            value={experimentDescription}
            onChange={(e) => setExperimentDescription(e.target.value)}
            className="form-control"
            rows={4}
          />
        </div>
        
        <div className="button-container">
          <button className="save-button" onClick={handleSave}>
            Save Changes
          </button>
        </div>
      </div>
      
      {/* Danger Zone Section */}
      <div className="settings-card danger-card">
        <div className="danger-content">
          <div>
            <h3>Danger Zone</h3>
            <p>Once you delete an experiment, there is no going back. Please be certain.</p>
          </div>
          <div className="button-container">
            <button className="delete-button" onClick={handleDelete}>
              Delete Experiment
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Settings;
