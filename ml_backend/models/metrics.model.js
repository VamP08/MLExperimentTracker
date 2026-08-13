import mongoose from "mongoose";

const { Schema, model, Types } = mongoose;

const metricSchema = new Schema({
  runId: {
    type: Types.ObjectId,
    ref: "Run",
    required: true
  },
  name: {
    type: String,
    required: true
  },
  value: {
    type: Number,
    required: true
  },
  step: Number, // optional for tracking (e.g., epoch number)
  timestamp: {
    type: Date,
    default: Date.now
  }
}, { timestamps: true });

export default model("Metric", metricSchema);
