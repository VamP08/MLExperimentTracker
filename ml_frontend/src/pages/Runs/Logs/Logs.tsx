import './Logs.css';

const Logs = () => {
  // Sample logs data
  const logs = [
    { timestamp: '2023-06-15 09:30:05', level: 'INFO', message: 'Run started' },
    { timestamp: '2023-06-15 09:30:10', level: 'INFO', message: 'Loading dataset...' },
    { timestamp: '2023-06-15 09:30:15', level: 'INFO', message: 'Dataset loaded: 10000 samples' },
    { timestamp: '2023-06-15 09:30:20', level: 'INFO', message: 'Initializing model with parameters: learning_rate=0.001, batch_size=32' },
    { timestamp: '2023-06-15 09:35:30', level: 'INFO', message: 'Epoch 1/100, Loss: 0.723, Accuracy: 0.78' },
    { timestamp: '2023-06-15 09:40:45', level: 'INFO', message: 'Epoch 2/100, Loss: 0.512, Accuracy: 0.83' },
    { timestamp: '2023-06-15 09:45:55', level: 'WARNING', message: 'Learning rate may be too high, consider reducing it' },
    { timestamp: '2023-06-15 09:50:10', level: 'INFO', message: 'Epoch 3/100, Loss: 0.345, Accuracy: 0.87' },
    { timestamp: '2023-06-15 10:55:20', level: 'ERROR', message: 'Out of memory error during batch processing' },
    { timestamp: '2023-06-15 10:55:25', level: 'INFO', message: 'Reducing batch size to 16 and continuing' },
    { timestamp: '2023-06-15 11:10:30', level: 'INFO', message: 'Training completed' },
    { timestamp: '2023-06-15 11:14:45', level: 'INFO', message: 'Final metrics: Loss: 0.087, Accuracy: 0.92' },
    { timestamp: '2023-06-15 11:15:00', level: 'INFO', message: 'Run completed successfully' }
  ];

  return (
    <div className="logs-page">
      <div className="logs-header">
        <h2>Run Logs</h2>
        <div className="logs-controls">
          <select className="log-level-filter">
            <option value="all">All Levels</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select>
          <button className="download-logs-button">Download Logs</button>
        </div>
      </div>

      <div className="logs-container">
        {logs.map((log, index) => (
          <div key={index} className={`log-entry ${log.level.toLowerCase()}`}>
            <span className="log-timestamp">{log.timestamp}</span>
            <span className={`log-level ${log.level.toLowerCase()}`}>{log.level}</span>
            <span className="log-message">{log.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Logs;