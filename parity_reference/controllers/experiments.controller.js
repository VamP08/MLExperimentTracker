import experimentService from "../services/experiment.service.js";

// GET /experiment/all — Get all experiments
export const getAllExperiments = async (req, res) => {
  try {
    const experiments = experimentService.getAllExperiments();
    res.json(experiments);
  } catch (err) {
    console.error("Error fetching experiments:", err);
    res.status(500).json({ message: "Failed to fetch experiments" });
  }
};

// GET /experiment/:id — Specific experiment
export const getExperimentDetails = async (req, res) => {
  try {
    const { id } = req.params;
    const experiment = experimentService.getExperimentByName(id);
    
    if (!experiment) {
      return res.status(404).json({ message: "Experiment not found" });
    }

    res.json(experiment);
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /experiment — Latest experiment (default)
export const getLatestExperimentDetails = async (req, res) => {
  try {
    const experiment = experimentService.getLatestExperiment();
    
    if (!experiment) {
      return res.status(404).json({ message: "No experiments found" });
    }

    res.json(experiment);
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error" });
  }
};

// PATCH /api/experiment/:id — update description
export const updateExperimentDescription = async (req, res) => {
  try {
    const { id } = req.params;
    const { description } = req.body;

    const success = experimentService.updateDescription(id, description);
    
    if (!success) {
      return res.status(404).json({ message: "Experiment not found" });
    }

    res.json({ message: "Description updated", description });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /api/experiment/:id/runs — Get all runs for an experiment
export const getExperimentRuns = async (req, res) => {
  try {
    const { id } = req.params;
    const runs = experimentService.getRunsForExperiment(id);
    res.json(runs);
  } catch (err) {
    console.error("Error fetching runs:", err);
    res.status(500).json({ message: "Failed to fetch runs" });
  }
};
