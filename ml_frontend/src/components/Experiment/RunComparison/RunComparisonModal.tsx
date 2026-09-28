import { useEffect, useRef } from 'react';
import { FiX } from 'react-icons/fi';
import RunComparison from './RunComparison';
import './RunComparisonModal.css';

interface Props {
  experimentId: string;
  /** The runs the user selected before opening the comparison. */
  selectedRunIds: string[];
  onClose: () => void;
}

/**
 * Dialog shell around `RunComparison` — the "Compare selected" button in the
 * experiment's runs table opens this (GAPS M11). The comparison itself owns
 * its own fetching and states; this file owns only dismissal and focus.
 */
const RunComparisonModal = ({ experimentId, selectedRunIds, onClose }: Props) => {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  // The caller passes a fresh closure each render; reading it through a ref keeps the
  // effect below to one run per open, so focus is not bounced on every parent render.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };

    // Focus goes into the dialog, and back to whatever opened it on close.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.addEventListener('keydown', onKeyDown);
    closeButtonRef.current?.focus();

    // The page behind a full-height dialog should not scroll with it.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      opener?.focus();
    };
  }, []);

  return (
    <div className="cmpmodal-overlay" onClick={onClose} role="presentation">
      <div
        className="cmpmodal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cmpmodal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="cmpmodal-head">
          <h2 id="cmpmodal-title">Run comparison</h2>
          <button
            type="button"
            ref={closeButtonRef}
            className="btn btn-icon btn-ghost"
            onClick={onClose}
            aria-label="Close comparison"
          >
            <FiX />
          </button>
        </div>
        <div className="cmpmodal-body">
          <RunComparison experimentId={experimentId} initialSelectedRunIds={selectedRunIds} />
        </div>
      </div>
    </div>
  );
};

export default RunComparisonModal;
