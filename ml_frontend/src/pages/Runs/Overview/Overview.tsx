import Information from '../../../components/Runs/Overview/Information/Information';
import RunParams from '../../../components/Runs/Overview/RunParam/RunParams';
import Metrics from '../../../components/Runs/Overview/Metrics/Metrics';
import Tags from '../../../components/Runs/Overview/Tags/Tags';
import Insights from '../../../components/Runs/Overview/Insights/Insights';
import Description from '../../../components/Runs/Overview/Description/Description';
import Provenance from '../../../components/Runs/Provenance/Provenance';
import './Overview.css';
import { useEffect, useState } from 'react';

interface OverviewProps {
  runId: string;
}

interface ParametersData {
  name: string;
  value: number;
}

interface MetricsData {
  name: string;
  value: number;
}

// GAPS M13: these are the keys the API actually sends. The response is cast
// straight into this interface, so a name that is wrong here renders blank
// forever with nothing to catch it — `Starttime`/`Endtime` did exactly that.
interface RunInformation {
  _id: string;
  status: string;
  startTime: string | null;
  endTime: string | null;
  duration: number;
  tags: string[];
}

interface RunInsight {
  parameters: number;
  metrics: number;
  topMetric: {
    name: string;
    value: string;
  };
}

const Overview = ( {runId}: OverviewProps) => {
  const [parametersData, setParametersData] = useState<ParametersData[]>([]);
  const [metricsData, setMetricsData] = useState<MetricsData[]>([]);
  const [run, setRunData] = useState<RunInformation | null>(null);
  const [insight, setRunInsight] = useState<RunInsight | null>(null);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [description, setDescription] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchOverview = async () => {
      if (!runId) {
        setLoading(false);
        return;
      }

      try{
        setLoading(true);
        setError(null);

        const res = await fetch(`/api/run/${runId}`);
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);

        const data = await res.json();

        setParametersData(
          Object.entries(data.parameters || {}).map(([name, value]) => ({
            name,
            value: value as number
          }))
        );

        const mappedMetrics = Object.entries(data.metrics || {}).map(([name, value]) => ({
          name,
          value: value as number
        }));

        setMetricsData(mappedMetrics);
        
        // Find the metric with the highest value (assuming numeric values)
        let topMetric = { name: "N/A", value: "N/A" };

        if (mappedMetrics.length > 0) {
          const sorted = [...mappedMetrics].sort((a, b) => b.value - a.value);
          topMetric = {
            name: sorted[0].name,
            value: sorted[0].value.toString()
          };
        }

        setRunInsight({
          parameters: Object.keys(data.parameters || {}).length,
          metrics: mappedMetrics.length,
          topMetric
        });
        setRunData(data)
        setDescription(data.description || "");
        setAllTags(data.tags || []);

      }
      catch (err) {
        // GAPS M24: this used to be a bare console.error, which left the page
        // showing an empty shell that was indistinguishable from a slow load.
        setError(err instanceof Error ? err.message : "Unknown error");
        setRunData(null);
      }
      finally {
        setLoading(false);
      }
    };

    fetchOverview();
  }, [runId]);

  const handleAddTag = async (newTag: string) => {
  if (!allTags.includes(newTag)) {
    const updatedTags = [...allTags, newTag];
    setAllTags(updatedTags);

    try {
      await fetch(`/api/run/${runId}/tags`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags: updatedTags }),
      });
    } catch (error) {
      console.error('Failed to update tags:', error);
    }
  }};

  const handleEditDescription = async (newDesc: string) => {
  setDescription(newDesc);

  try {
    await fetch(`/api/run/${runId}/description`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description: newDesc }),
    });
  } catch (error) {
    console.error('Failed to update description:', error);
  }};

  if (loading) {
    return <div className="run-overview-message">Loading run overview...</div>;
  }

  if (error) {
    return (
      <div className="run-overview-message run-overview-error">
        Could not load this run: {error}
      </div>
    );
  }

  return (
    <div className="run-overview-container">
      <div className="left-column">
        <div className="card params-card">
          <h2 className="section-title">Run Parameters</h2>
          <RunParams params={parametersData} />
        </div>
        <div className="card metrics-card">
          <h2 className="section-title">Metrics</h2>
          <Metrics metrics={metricsData} />
        </div>
      </div>
      <div className="right-column">
        <div className="card information-card">
          {run && <Information runInfo={run} />}
        </div>
        <div className="card tags-card">
          <Tags 
            tags={allTags} 
            onAddTag={handleAddTag}
          />
        </div>
        <div className="card description-card">
          <Description
            description={description}
            onEdit={handleEditDescription}
          />
        </div>
        <div className="card insights-card">
          {insight && <Insights insights={insight} />}
        </div>
        {/* Carries its own card, because a run with no manifest — every run written
            before format 1.1 — must render nothing rather than an empty card. */}
        <Provenance runId={runId} />
      </div>
    </div>
  );
};

export default Overview;
