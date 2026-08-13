import React from 'react';
import './Tags.css';

interface TagsProps {
  tags: string[];
  selectedTags: string[];
  onTagSelect: (tag: string) => void;
}

const Tags: React.FC<TagsProps> = ({ tags, selectedTags, onTagSelect }) => {
  return (
    <div className="tags">
      <h2 className="tags-title">Filter by Tags</h2>
      <div className="tags-container">
        {tags.map((tag, i) => (
          <button
            key={i}
            onClick={() => onTagSelect(tag)}
            className={`tag-button ${selectedTags.includes(tag) ? 'selected' : ''}`}
          >
            {tag}
          </button>
        ))}
      </div>
    </div>
  );
};

export default Tags;
