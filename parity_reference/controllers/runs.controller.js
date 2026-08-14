import runService from "../services/run.service.js";

// GET /run/:id - Specific Run
export const getRunDetails = async (req, res) => {
  try {
    const { id } = req.params;
    const run = runService.findRunById(id);
    
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    res.json(run);
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error" });
  }
};

//GET /run - Latest Run
export const getLatestRunDetails = async (req, res) => {
  try {
    const run = runService.getLatestRun();
    
    if (!run) {
      return res.status(404).json({ message: "No runs found" });
    }

    res.json(run);
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error" });
  }
};

// PATCH /run/:id/tags
export const updateRunTags = async (req, res) => {
  try {
    const { id } = req.params;
    const { tags } = req.body;

    if (!Array.isArray(tags)) {
      return res.status(400).json({ message: "Tags must be an array" });
    }

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    const success = runService.updateRunTags(run.experimentId, id, tags);
    
    if (!success) {
      return res.status(500).json({ message: "Failed to update tags" });
    }

    res.json({ message: "Tags updated", tags });
  } catch (err) {
    console.error("Failed to update tags:", err);
    res.status(500).json({ message: "Server error" });
  }
};

// PATCH /run/:id/description
export const updateRunDescription = async (req, res) => {
  try {
    const { id } = req.params;
    const { description } = req.body;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    const success = runService.updateRunDescription(run.experimentId, id, description);
    
    if (!success) {
      return res.status(500).json({ message: "Failed to update description" });
    }

    res.json({ message: "Description updated", description });
  } catch (err) {
    console.error("Failed to update description:", err);
    res.status(500).json({ message: "Server error" });
  }
};
