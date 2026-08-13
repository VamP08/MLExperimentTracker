import { useEffect, useState } from "react";
import "./Settings.css";

type ThemeMode = "light" | "dark";

const Settings = () => {
  const [theme, setTheme] = useState<ThemeMode>("dark");

  // Initialize theme from localStorage or default to dark
  useEffect(() => {
    const savedTheme = localStorage.getItem("theme") as ThemeMode | null;
    if (savedTheme) {
      setTheme(savedTheme);
      document.documentElement.setAttribute("data-theme", savedTheme);
    } else {
      // Default to dark mode
      setTheme("dark");
      document.documentElement.setAttribute("data-theme", "dark");
      localStorage.setItem("theme", "dark");
    }
  }, []);

  const handleThemeChange = (newTheme: ThemeMode) => {
    setTheme(newTheme);
    document.documentElement.setAttribute("data-theme", newTheme);
    localStorage.setItem("theme", newTheme);
  };

  return (
    <div className="settings-container">
      <h1 className="settings-title">Settings</h1>
      
      <div className="settings-section">
        <h2 className="section-title">Appearance</h2>
        
        <div className="theme-selector">
          <h3 className="setting-label">Theme</h3>
          <div className="theme-options">
            <button 
              className={`theme-button ${theme === "light" ? "active" : ""}`}
              onClick={() => handleThemeChange("light")}
            >
              Light
            </button>
            <button 
              className={`theme-button ${theme === "dark" ? "active" : ""}`}
              onClick={() => handleThemeChange("dark")}
            >
              Dark
            </button>
          </div>
          <p className="theme-description">
            Choose between light and dark mode. This setting will override your system preferences.
          </p>
        </div>
      </div>
    </div>
  );
};

export default Settings;