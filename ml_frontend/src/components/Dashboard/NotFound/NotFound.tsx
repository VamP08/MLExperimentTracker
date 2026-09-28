import { Link, useLocation } from "react-router-dom";
import TopBar from "../../TopBar/TopBar";
import "./NotFound.css";

/**
 * Catch-all view for unmatched routes (GAPS N15). Without it an unknown URL renders the
 * sidebar beside an empty main area, which is indistinguishable from a page that failed
 * to load.
 */
const NotFound = () => {
  const { pathname } = useLocation();

  return (
    <>
      <TopBar crumbs={[{ label: "Dashboard", to: "/" }, { label: "Not found" }]} />
      <div className="page">
        <section className="panel notfound" aria-labelledby="notfound-title">
          <div className="state">
            <h3 id="notfound-title">Page not found</h3>
            <p>
              Nothing is routed at <code className="notfound-path">{pathname}</code>.
            </p>
            <p className="notfound-back">
              <Link to="/">Back to the dashboard</Link>
            </p>
          </div>
        </section>
      </div>
    </>
  );
};

export default NotFound;
