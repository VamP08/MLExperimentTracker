import TopBar from "../../components/TopBar/TopBar";
import { setTheme, useTheme } from "../../lib/theme";
import type { Theme } from "../../lib/theme";
import "./Settings.css";

const THEMES: { id: Theme; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

const Settings = () => {
  const theme = useTheme();

  return (
    <>
      <TopBar crumbs={[{ label: "Settings" }]} />
      <div className="page">
        <div className="page-head">
          <h1>Settings</h1>
        </div>

        <section className="panel settings-panel" aria-labelledby="settings-appearance">
          <div className="panel-head">
            <h2 id="settings-appearance">Appearance</h2>
          </div>
          <div className="panel-body settings-row">
            <div>
              <h3 id="settings-theme">Theme</h3>
              <p className="muted settings-help">
                Starts from your system setting. A choice made here is remembered in this browser.
              </p>
            </div>
            <div className="segmented" role="group" aria-labelledby="settings-theme">
              {THEMES.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className="btn"
                  aria-pressed={theme === option.id}
                  onClick={() => setTheme(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>
    </>
  );
};

export default Settings;
