import React, { useCallback, useEffect, useState } from 'react';
import './Evaluation.css';
import ConfusionMatrix from '../../../components/Runs/ConfusionMatrix/ConfusionMatrix';
import ROCCurve from '../../../components/Runs/ROCCurve/ROCCurve';
import FeatureImportance from '../../../components/Runs/FeatureImportance/FeatureImportance';

interface Props {
  runId: string;
}

// The recognised `type` literals from artifacts.jsonl. Each renderer accepts two
// spellings, and both are listed here so the tab and the renderer agree on what
// counts as present.
const MATRIX_TYPES = ['confusion_matrix', 'classification_report'];
const ROC_TYPES = ['roc_curve', 'roc_auc'];
const IMPORTANCE_TYPES = ['feature_importance', 'feature_importances'];

const Evaluation: React.FC<Props> = ({ runId }) => {
  const [types, setTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchArtifactTypes = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`/api/run/${runId}/artifacts`);
      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }

      const data = await response.json();
      const records: Array<{ type?: unknown }> = Array.isArray(data) ? data : [];
      setTypes(
        records
          .map((record) => record.type)
          .filter((type): type is string => typeof type === 'string')
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setTypes([]);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchArtifactTypes();
  }, [runId, fetchArtifactTypes]);

  if (loading) {
    return <div className="evaluation-message">Loading evaluation artifacts...</div>;
  }

  if (error) {
    return (
      <div className="evaluation-message evaluation-message-error">
        Could not load artifacts: {error}
      </div>
    );
  }

  const hasMatrix = types.some((type) => MATRIX_TYPES.includes(type));
  const hasROC = types.some((type) => ROC_TYPES.includes(type));
  const hasImportance = types.some((type) => IMPORTANCE_TYPES.includes(type));

  // Most runs log none of these. Saying so once, quietly, is the whole of the
  // empty state — it is not a failure and there is nothing to retry.
  if (!hasMatrix && !hasROC && !hasImportance) {
    return (
      <div className="evaluation-message">
        This run logged no evaluation artifacts. Confusion matrices, ROC curves and feature
        importances appear here once a run writes them to <code>artifacts.jsonl</code>.
      </div>
    );
  }

  return (
    <div className="evaluation">
      {hasMatrix && (
        <section className="evaluation-panel">
          <ConfusionMatrix runId={runId} />
        </section>
      )}
      {hasROC && (
        <section className="evaluation-panel">
          <ROCCurve runId={runId} />
        </section>
      )}
      {hasImportance && (
        <section className="evaluation-panel">
          <FeatureImportance runId={runId} />
        </section>
      )}
    </div>
  );
};

export default Evaluation;
