import express from "express";
import { dashboard } from "../controllers/dashboard.controller.js";

const router = express.Router();

// GET /api/dashboard
router.get("/", dashboard);

export default router;
