import type React from 'react';
import { Link, useLocation } from 'react-router-dom';
import './NotFound.css';

/**
 * Catch-all view for unmatched routes (GAPS N15). Without it an unknown URL renders the
 * sidebar beside an empty main area, which is indistinguishable from a page that failed
 * to load.
 */
const NotFound: React.FC = () => {
  const { pathname } = useLocation();

  return (
    <section className="notfound" aria-labelledby="notfound-title">
      <p className="notfound-code">404</p>
      <h1 className="notfound-title" id="notfound-title">
        Page not found
      </h1>
      <p className="notfound-text">
        Nothing is routed at <code className="notfound-path">{pathname}</code>.
      </p>
      <Link className="notfound-link" to="/">
        Back to the dashboard
      </Link>
    </section>
  );
};

export default NotFound;
