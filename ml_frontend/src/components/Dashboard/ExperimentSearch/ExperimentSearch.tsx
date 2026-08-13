import React, { useState } from 'react';
import './ExperimentSearch.css';

interface Run {
  _id: string;
  name: string;
  status: string;
  startedAt: string;
}

interface Experiment {
  _id: string;
  name: string;
  description?: string;
  tags: string[];
  createdAt?: string;
  runs: Run[];
  stats: {
    totalRuns: number;
    completedRuns: number;
    successRate: string;
  };
}

interface Props {
  experiments: Experiment[];
  onFilterChange: (filtered: Experiment[]) => void;
}

const ExperimentSearch: React.FC<Props> = ({ experiments, onFilterChange }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState<'name' | 'date' | 'runs'>('date');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Get all unique tags
  const allTags = Array.from(
    new Set(experiments.flatMap(exp => exp.tags))
  ).filter(tag => tag);

  React.useEffect(() => {
    const filterExperiments = () => {
      let filtered = [...experiments];

      // Filter by search term
      if (searchTerm) {
        const term = searchTerm.toLowerCase();
        filtered = filtered.filter(exp =>
          exp.name.toLowerCase().includes(term) ||
          (exp.description && exp.description.toLowerCase().includes(term)) ||
          exp.tags.some(tag => tag.toLowerCase().includes(term))
        );
      }

      // Filter by selected tags
      if (selectedTags.length > 0) {
        filtered = filtered.filter(exp =>
          selectedTags.some(tag => exp.tags.includes(tag))
        );
      }

      // Sort
      filtered.sort((a, b) => {
        let comparison = 0;
        
        switch (sortBy) {
          case 'name':
            comparison = a.name.localeCompare(b.name);
            break;
          case 'date':
            comparison = new Date(a.createdAt || 0).getTime() - new Date(b.createdAt || 0).getTime();
            break;
          case 'runs':
            comparison = (a.stats?.totalRuns || 0) - (b.stats?.totalRuns || 0);
            break;
        }

        return sortOrder === 'asc' ? comparison : -comparison;
      });

      onFilterChange(filtered);
    };

    filterExperiments();
  }, [searchTerm, selectedTags, sortBy, sortOrder, experiments, onFilterChange]);

  const toggleTag = (tag: string) => {
    setSelectedTags(prev =>
      prev.includes(tag)
        ? prev.filter(t => t !== tag)
        : [...prev, tag]
    );
  };

  const clearFilters = () => {
    setSearchTerm('');
    setSelectedTags([]);
    setSortBy('date');
    setSortOrder('desc');
  };

  return (
    <div className="experiment-search">
      <div className="search-section">
        <div className="search-bar">
          <input
            type="text"
            placeholder="Search experiments by name, description, or tags..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
          {searchTerm && (
            <button 
              className="clear-search"
              onClick={() => setSearchTerm('')}
            >
              ✕
            </button>
          )}
        </div>

        <div className="filter-controls">
          <div className="sort-controls">
            <label>Sort by:</label>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as 'name' | 'date' | 'runs')}>
              <option value="date">Date Created</option>
              <option value="name">Name</option>
              <option value="runs">Total Runs</option>
            </select>
            <button
              className={`sort-order ${sortOrder}`}
              onClick={() => setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')}
              title={sortOrder === 'asc' ? 'Ascending' : 'Descending'}
            >
              {sortOrder === 'asc' ? '↑' : '↓'}
            </button>
          </div>

          {(searchTerm || selectedTags.length > 0 || sortBy !== 'date' || sortOrder !== 'desc') && (
            <button className="clear-all" onClick={clearFilters}>
              Clear All Filters
            </button>
          )}
        </div>
      </div>

      {allTags.length > 0 && (
        <div className="tags-section">
          <label>Filter by tags:</label>
          <div className="tags-container">
            {allTags.map(tag => (
              <button
                key={tag}
                className={`tag-filter ${selectedTags.includes(tag) ? 'active' : ''}`}
                onClick={() => toggleTag(tag)}
              >
                {tag}
                {selectedTags.includes(tag) && <span className="tag-check">✓</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="results-summary">
        Showing {experiments.length} experiment{experiments.length !== 1 ? 's' : ''}
        {searchTerm && ` matching "${searchTerm}"`}
        {selectedTags.length > 0 && ` with tags: ${selectedTags.join(', ')}`}
      </div>
    </div>
  );
};

export default ExperimentSearch;
