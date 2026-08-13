import express from "express";
import {
  getExperimentDetails,
  getLatestExperimentDetails,
  updateExperimentDescription,
  getExperimentRuns,
  getAllExperiments
} from "../controllers/experiments.controller.js";

const router = express.Router();

// Note: Order matters - more specific routes should come before generic ones
router.get("/all", getAllExperiments);       // /experiment/all - get all experiments
router.get("/:id/runs", getExperimentRuns);  // /experiment/:id/runs
router.get("/:id", getExperimentDetails);    // /experiment/:id
router.get("/", getLatestExperimentDetails); // /experiment
router.patch("/:id", updateExperimentDescription);

export default router;
