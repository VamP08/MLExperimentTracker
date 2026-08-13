import React from 'react';
import './Tags.css';

interface TagsProps {
  tags: string[];
  onAddTag?: (newTag: string) => void; 
}

const Tags: React.FC<TagsProps> = ({ tags, onAddTag }) => {
  return (
    <div className="run-tags">
      <h3 className="tags-title">Tags</h3>
      <div className="tags-container">
        {tags.map((tag, index) => (
          <span key={index} className="tag">
            {tag}
          </span>
        ))}
      <button
        className="add-tag-button"
       onClick={() => {
        const newTag = prompt("Enter new tag:");
        if (newTag && onAddTag) onAddTag(newTag);
        }} > + Add Tag </button>
      </div>
    </div>
  );
};

export default Tags;
