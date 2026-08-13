// src/components/dashboard/RecentSearches.tsx
import React, { useState } from 'react';
import './RecentSearches.css';

interface RecentSearchesProps {
  searches: string[];
}

const RecentSearches: React.FC<RecentSearchesProps> = ({ searches }) => {
  const [showAll, setShowAll] = useState(false);
  const displayCount = showAll ? searches.length : Math.min(3, searches.length);
  const displayedSearches = searches.slice(0, displayCount);
  
  const handleShowMore = () => {
    setShowAll(!showAll);
  };

  return (
    <div className="recent-searches">
      <h2 className="searches-title">Recent Searches</h2>
      <ul className="searches-list">
        {displayedSearches.map((search, i) => (
          <li key={i} className="search-item">
            <span>{search}</span>
          </li>
        ))}
      </ul>
      {searches.length > 3 && (
        <button className="show-more-button" onClick={handleShowMore}>
          {showAll ? 'Show Less' : 'Show More...'}
        </button>
      )}
    </div>
  );
};

export default RecentSearches;
