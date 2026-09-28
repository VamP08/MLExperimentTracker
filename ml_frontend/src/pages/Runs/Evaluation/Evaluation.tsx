import React, { useCallback, useEffect, useState } from 'react';
import ConfusionMatrix from '../../../components/Runs/ConfusionMatrix/ConfusionMatrix';
import ROCCurve from '../../../components/Runs/ROCCurve/ROCCurve';
import FeatureImportance from '../../../components/Runs/FeatureImportance/FeatureImportance';
import { apiFetch } from '../../../lib/api';

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
      const response = await apiFetch(`/api/run/${runId}/artifacts`);
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
    return (
      <div className="stack">
        <section className="panel">
          <div className="state">Loading evaluation artifacts…</div>
        </section>
      </div>
    );
  }

  if (error) {
    return (
      <div className="stack">
        <section className="panel">
          <div className="state error">Could not load artifacts: {error}</div>
        </section>
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
      <div className="stack">
        <section className="panel">
          <div className="state">
            <h3>No evaluation artifacts</h3>
            <p>
              This run logged no evaluation artifacts. Confusion matrices, ROC curves and feature
              importances appear here once a run writes them to <code>artifacts.jsonl</code>.
            </p>
          </div>
        </section>
      </div>
    );
  }

  // Each renderer draws its own panel, heading included.
  return (
    <div className="stack">
      {hasMatrix && <ConfusionMatrix runId={runId} />}
      {hasROC && <ROCCurve runId={runId} />}
      {hasImportance && <FeatureImportance runId={runId} />}
    </div>
  );
};

export default Evaluation;
