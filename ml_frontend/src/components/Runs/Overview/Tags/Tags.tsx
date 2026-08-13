import React, { useEffect, useRef, useState } from 'react';
import './Tags.css';

interface TagsProps {
  tags: string[];
  onAddTag?: (newTag: string) => void;
}

const Tags: React.FC<TagsProps> = ({ tags, onAddTag }) => {
  // GAPS N20: adding a tag used to go through window.prompt(), which is a
  // blocking browser chrome dialog with no styling and no keyboard affordances
  // beyond OK/Cancel. It is an inline field now.
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (adding) inputRef.current?.focus();
  }, [adding]);

  const close = () => {
    setAdding(false);
    setDraft('');
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = draft.trim();
    // A duplicate is silently ignored rather than reported: the tag the user
    // asked for is already on the run, so the request is satisfied.
    if (trimmed && onAddTag) onAddTag(trimmed);
    close();
  };

  return (
    <div className="run-tags">
      <h3 className="tags-title">Tags</h3>
      <div className="tags-container">
        {tags.map((tag) => (
          <span key={tag} className="tag">
            {tag}
          </span>
        ))}

        {tags.length === 0 && !adding && (
          <span className="run-tags-empty">No tags on this run.</span>
        )}

        {adding ? (
          <form className="run-tags-form" onSubmit={submit}>
            <label className="run-tags-label" htmlFor="run-tags-input">
              New tag
            </label>
            <input
              id="run-tags-input"
              ref={inputRef}
              className="run-tags-input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') close();
              }}
              placeholder="baseline"
              maxLength={64}
            />
            <button className="run-tags-confirm" type="submit">
              Add
            </button>
            <button className="run-tags-cancel" type="button" onClick={close}>
              Cancel
            </button>
          </form>
        ) : (
          <button className="add-tag-button" type="button" onClick={() => setAdding(true)}>
            + Add Tag
          </button>
        )}
      </div>
    </div>
  );
};

export default Tags;
