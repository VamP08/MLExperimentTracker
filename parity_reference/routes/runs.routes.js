import express from 'express';
import {
  getRunDetails,
  getLatestRunDetails,
  updateRunTags,
  updateRunDescription
} from '../controllers/runs.controller.js';

const router = express.Router();

router.get('/:id', getRunDetails);
router.get('/', getLatestRunDetails);
router.patch('/:id/tags', updateRunTags);
router.patch('/:id/description', updateRunDescription);

export default router;
