import { Link } from "react-router-dom";
import "./Breadcrumbs.css";

/** `to` is left out on the last crumb and on any crumb whose target isn't known. */
export interface Crumb {
  label: string;
  to?: string;
}

interface BreadcrumbsProps {
  items: Crumb[];
}

/** Trail above a page header: `Dashboard / <experiment> / <run>`. */
const Breadcrumbs = ({ items }: BreadcrumbsProps) => {
  if (items.length === 0) return null;

  return (
    <nav aria-label="Breadcrumb" className="breadcrumbs">
      <ol className="breadcrumbs-list">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;

          return (
            <li className="breadcrumbs-item" key={`${index}-${item.label}`}>
              {index > 0 && (
                <span className="breadcrumbs-separator" aria-hidden="true">
                  /
                </span>
              )}
              {isLast || !item.to ? (
                <span
                  className={isLast ? "breadcrumbs-current" : "breadcrumbs-text"}
                  aria-current={isLast ? "page" : undefined}
                  title={item.label}
                >
                  {item.label}
                </span>
              ) : (
                <Link className="breadcrumbs-link" to={item.to} title={item.label}>
                  {item.label}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

export default Breadcrumbs;
