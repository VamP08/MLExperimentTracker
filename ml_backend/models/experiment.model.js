import mongoose from "mongoose";

const { Schema, model } = mongoose;

const experimentSchema = new Schema({
  name: { type: String, required: true },
  description: String,
  tags: [{ type: String }],
  activityTimeline: [{
    date: { type: Date, default: Date.now },
    event: { type: String }
  }] 
}, {
  timestamps: true,
  toJSON: { virtuals: true },
  toObject: { virtuals: true }
});

// ✅ Virtual for runs
experimentSchema.virtual("runs", {
  ref: "Run",
  localField: "_id",
  foreignField: "experimentId"
});

export default model("Experiment", experimentSchema);
