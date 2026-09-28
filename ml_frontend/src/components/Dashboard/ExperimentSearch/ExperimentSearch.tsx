import { useEffect, useMemo, useState } from 'react';
import { FiArrowDown, FiArrowUp, FiCheck, FiSearch, FiX } from 'react-icons/fi';
import { toTimestamp } from '../format';
import './ExperimentSearch.css';

/** Minimal shape this control needs. Callers keep their richer type and get it back as-is. */
export interface FilterableExperiment {
  name: string;
  description?: string;
  tags: string[];
  createdAt?: string | null;
  stats?: { totalRuns?: number };
}

interface Props<T extends FilterableExperiment> {
  experiments: T[];
  /** Called with the filtered, sorted list whenever the criteria or the input change. */
  onFilterChange: (filtered: T[]) => void;
}

type SortKey = 'name' | 'date' | 'runs';
type SortOrder = 'asc' | 'desc';

/** Tags come from metadata.json on disk, so don't trust their shape. */
const cleanTags = (tags: unknown): string[] =>
  Array.isArray(tags) ? tags.filter((t): t is string => typeof t === 'string' && t !== '') : [];

function ExperimentSearch<T extends FilterableExperiment>({ experiments, onFilterChange }: Props<T>) {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState<SortKey>('date');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  const allTags = useMemo(
    () => Array.from(new Set(experiments.flatMap((exp) => cleanTags(exp.tags)))).sort(),
    [experiments],
  );

  // Computed during render so the count below always matches the list the caller shows.
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
          comparison = toTimestamp(a.createdAt) - toTimestamp(b.createdAt);
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
    <section className="exp-search" role="search" aria-label="Search and filter experiments">
      <div className="exp-search-row">
        <div className="exp-search-field">
          <FiSearch className="exp-search-icon" aria-hidden="true" />
          <input
            id="experiment-search-input"
            type="search"
            className="input exp-search-input"
            placeholder="Search by name, description or tag"
            aria-label="Search experiments by name, description, or tag"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          {searchTerm && (
            <button
              type="button"
              className="btn btn-icon btn-ghost exp-search-clear"
              aria-label="Clear the search term"
              onClick={() => setSearchTerm('')}
            >
              <FiX />
            </button>
          )}
        </div>

        <div className="exp-search-sort">
          <label className="muted" htmlFor="experiment-search-sort-key">
            Sort by
          </label>
          <select
            id="experiment-search-sort-key"
            className="select"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortKey)}
          >
            <option value="date">Date created</option>
            <option value="name">Name</option>
            <option value="runs">Total runs</option>
          </select>
          <button
            type="button"
            className="btn btn-icon"
            onClick={() => setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
            aria-label={
              sortOrder === 'asc' ? 'Sorted ascending, switch to descending' : 'Sorted descending, switch to ascending'
            }
            title={sortOrder === 'asc' ? 'Ascending' : 'Descending'}
          >
            {sortOrder === 'asc' ? <FiArrowUp /> : <FiArrowDown />}
          </button>
          {!isDefault && (
            <button type="button" className="btn btn-ghost" onClick={clearFilters}>
              Reset
            </button>
          )}
        </div>
      </div>

      {allTags.length > 0 && (
        // Collapsed by default so a long tag list doesn't push the experiments down.
        // Opens whenever a tag is selected so an active filter is visible.
        <details className="exp-search-tags" open={selectedTags.length > 0 || undefined}>
          <summary className="muted" id="experiment-search-tags-label">
            Filter by tag
            <span className="count">{selectedTags.length ? `${selectedTags.length} of ${allTags.length}` : allTags.length}</span>
          </summary>
          <div className="exp-search-tag-list" role="group" aria-labelledby="experiment-search-tags-label">
            {allTags.map((tag) => {
              const active = selectedTags.includes(tag);
              return (
                <button
                  type="button"
                  key={tag}
                  className="exp-search-tag"
                  aria-pressed={active}
                  onClick={() => toggleTag(tag)}
                >
                  {active && <FiCheck aria-hidden="true" />}
                  {tag}
                </button>
              );
            })}
          </div>
        </details>
      )}

      {isFiltered && (
        <p className="exp-search-summary muted num" role="status" aria-live="polite">
          {filtered.length} of {experiments.length} experiment{experiments.length !== 1 ? 's' : ''}
          {searchTerm && ` matching “${searchTerm}”`}
          {selectedTags.length > 0 && ` tagged ${selectedTags.join(', ')}`}
        </p>
      )}
    </section>
  );
}

export default ExperimentSearch;
