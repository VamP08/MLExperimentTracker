import React, { useEffect, useRef } from 'react';
import RunComparison from './RunComparison';
import './RunComparisonModal.css';

interface Props {
  experimentId: string;
  /** The runs the user selected before opening the comparison. */
  selectedRunIds: string[];
  onClose: () => void;
}

/**
 * Dialog shell around `RunComparison` — the "Compare Selected Runs" button in
 * the experiment's runs table opens this (GAPS M11). The comparison itself owns
 * its own fetching and states; this file owns only dismissal and focus.
 */
const RunComparisonModal: React.FC<Props> = ({ experimentId, selectedRunIds, onClose }) => {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };

    document.addEventListener('keydown', onKeyDown);
    closeButtonRef.current?.focus();

    // The page behind a full-height dialog should not scroll with it.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="runcomparisonmodal-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="runcomparisonmodal-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Compare runs"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="runcomparisonmodal-bar">
          <span className="runcomparisonmodal-heading">Run comparison</span>
          <button
            type="button"
            ref={closeButtonRef}
            className="runcomparisonmodal-close"
            onClick={onClose}
            aria-label="Close comparison"
          >
            ×
          </button>
        </div>
        <div className="runcomparisonmodal-body">
          <RunComparison
            experimentId={experimentId}
            initialSelectedRunIds={selectedRunIds}
          />
        </div>
      </div>
    </div>
  );
};

export default RunComparisonModal;
