import './Settings.css';

const Settings = () => {
  return (
    <div className="settings-page">
      <div className="settings-header">
        <h2>Run Settings</h2>
        <p>Configure settings for this run</p>
      </div>

      <div className="settings-section">
        <h3 className="section-title">General</h3>
        
        <div className="setting-item">
          <div className="setting-info">
            <label className="setting-label">Run Name</label>
            <p className="setting-description">Change the display name of this run</p>
          </div>
          <div className="setting-control">
            <input type="text" className="setting-input" defaultValue="Run ABC-456" />
            <button className="save-button">Save</button>
          </div>
        </div>
        
        <div className="setting-item">
          <div className="setting-info">
            <label className="setting-label">Description</label>
            <p className="setting-description">Add a description to provide context for this run</p>
          </div>
          <div className="setting-control">
            <textarea 
              className="setting-textarea" 
              defaultValue="This run was executed to test the model performance with optimized hyperparameters."
            />
            <button className="save-button">Save</button>
          </div>
        </div>
      </div>

      <div className="settings-section">
        <h3 className="section-title">Tags</h3>
        
        <div className="setting-item">
          <div className="setting-info">
            <label className="setting-label">Run Tags</label>
            <p className="setting-description">Add tags to categorize and filter runs</p>
          </div>
          <div className="setting-control">
            <div className="tags-input-container">
              <div className="tag-pill">production <span className="remove-tag">×</span></div>
              <div className="tag-pill">experiment <span className="remove-tag">×</span></div>
              <div className="tag-pill">high-accuracy <span className="remove-tag">×</span></div>
              <input type="text" className="tag-input" placeholder="Add a tag..." />
            </div>
          </div>
        </div>
      </div>

      <div className="settings-section danger-zone">
        <h3 className="section-title">Danger Zone</h3>
        
        <div className="setting-item">
          <div className="setting-info">
            <label className="setting-label">Archive Run</label>
            <p className="setting-description">Archive this run to hide it from the main view</p>
          </div>
          <div className="setting-control">
            <button className="archive-button">Archive Run</button>
          </div>
        </div>
        
        <div className="setting-item">
          <div className="setting-info">
            <label className="setting-label">Delete Run</label>
            <p className="setting-description">Permanently delete this run and all associated data</p>
          </div>
          <div className="setting-control">
            <button className="delete-button">Delete Run</button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Settings;