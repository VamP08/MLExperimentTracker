import { useEffect, useMemo, useState } from 'react';
import './ExperimentSearch.css';

/**
 * The shape this control needs. It is deliberately a structural minimum rather than
 * the full dashboard experiment: the caller keeps its own richer type and gets it
 * back unchanged, so the filtered list can be rendered without a cast.
 */
export interface FilterableExperiment {
  name: string;
  description?: string;
  tags: string[];
  createdAt?: string;
  stats?: { totalRuns?: number };
}

interface Props<T extends FilterableExperiment> {
  experiments: T[];
  /** Called with the filtered, sorted list whenever the criteria or the input change. */
  onFilterChange: (filtered: T[]) => void;
}

type SortKey = 'name' | 'date' | 'runs';
type SortOrder = 'asc' | 'desc';

/** Tags arrive from disk and are only as well-formed as whoever wrote metadata.json. */
const cleanTags = (tags: unknown): string[] =>
  Array.isArray(tags) ? tags.filter((t): t is string => typeof t === 'string' && t !== '') : [];

const timestamp = (value: string | undefined): number => {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
};

function ExperimentSearch<T extends FilterableExperiment>({ experiments, onFilterChange }: Props<T>) {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState<SortKey>('date');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  const allTags = useMemo(
    () => Array.from(new Set(experiments.flatMap((exp) => cleanTags(exp.tags)))).sort(),
    [experiments],
  );

  // Derived, not stored: computing the result during render is what keeps the count in
  // the summary line below equal to the list the caller is actually showing.
  const filtered = useMemo(() => {
    let result = [...experiments];

    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      result = result.filter(
        (exp) =>
          exp.name.toLowerCase().includes(term) ||
          (exp.description ?? '').toLowerCase().includes(term) ||
          cleanTags(exp.tags).some((tag) => tag.toLowerCase().includes(term)),
      );
    }

    if (selectedTags.length > 0) {
      result = result.filter((exp) => {
        const tags = cleanTags(exp.tags);
        return selectedTags.some((tag) => tags.includes(tag));
      });
    }

    result.sort((a, b) => {
      let comparison = 0;
      switch (sortBy) {
        case 'name':
          comparison = a.name.localeCompare(b.name);
          break;
        case 'date':
          comparison = timestamp(a.createdAt) - timestamp(b.createdAt);
          break;
        case 'runs':
          comparison = (a.stats?.totalRuns ?? 0) - (b.stats?.totalRuns ?? 0);
          break;
      }
      return sortOrder === 'asc' ? comparison : -comparison;
    });

    return result;
  }, [experiments, searchTerm, selectedTags, sortBy, sortOrder]);

  useEffect(() => {
    onFilterChange(filtered);
  }, [filtered, onFilterChange]);

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  };

  const clearFilters = () => {
    setSearchTerm('');
    setSelectedTags([]);
    setSortBy('date');
    setSortOrder('desc');
  };

  const isFiltered = searchTerm !== '' || selectedTags.length > 0;
  const isDefault = !isFiltered && sortBy === 'date' && sortOrder === 'desc';

  return (
    <section className="experiment-search" role="search" aria-label="Search and filter experiments">
      <div className="experiment-search-bar">
        <input
          id="experiment-search-input"
          type="search"
          placeholder="Search experiments by name, description, or tag"
          aria-label="Search experiments by name, description, or tag"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="experiment-search-input"
        />
        {searchTerm && (
          <button
            type="button"
            className="experiment-search-clear"
            aria-label="Clear the search term"
            onClick={() => setSearchTerm('')}
          >
            ✕
          </button>
        )}
      </div>

      <div className="experiment-search-controls">
        <div className="experiment-search-sort">
          <label className="experiment-search-label" htmlFor="experiment-search-sort-key">
            Sort by
          </label>
          <select
            id="experiment-search-sort-key"
            className="experiment-search-select"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortKey)}
          >
            <option value="date">Date created</option>
            <option value="name">Name</option>
            <option value="runs">Total runs</option>
          </select>
          <button
            type="button"
            className="experiment-search-order"
            onClick={() => setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
            aria-label={
              sortOrder === 'asc' ? 'Sorted ascending, switch to descending' : 'Sorted descending, switch to ascending'
            }
          >
            {sortOrder === 'asc' ? '↑' : '↓'}
          </button>
        </div>

        {!isDefault && (
          <button type="button" className="experiment-search-reset" onClick={clearFilters}>
            Reset
          </button>
        )}
      </div>

      {allTags.length > 0 && (
        <div className="experiment-search-tags">
          <span className="experiment-search-label" id="experiment-search-tags-label">
            Filter by tag
          </span>
          <div className="experiment-search-tag-list" role="group" aria-labelledby="experiment-search-tags-label">
            {allTags.map((tag) => {
              const active = selectedTags.includes(tag);
              return (
                <button
                  type="button"
                  key={tag}
                  className={`experiment-search-tag ${active ? 'active' : ''}`}
                  aria-pressed={active}
                  onClick={() => toggleTag(tag)}
                >
                  {tag}
                  {active && <span className="experiment-search-tag-check" aria-hidden="true">✓</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {isFiltered && (
        <p className="experiment-search-summary" role="status" aria-live="polite">
          {filtered.length} of {experiments.length} experiment{experiments.length !== 1 ? 's' : ''}
          {searchTerm && ` matching “${searchTerm}”`}
          {selectedTags.length > 0 && ` tagged ${selectedTags.join(', ')}`}
        </p>
      )}
    </section>
  );
}

export default ExperimentSearch;
