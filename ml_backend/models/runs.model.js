import mongoose from "mongoose";

const { Schema, model, Types } = mongoose;

const runSchema = new Schema({
    experimentId: { 
        type: Types.ObjectId, 
        ref: "Experiment", 
        required: true 
    },
    name: { 
        type: String, 
        required: true 
    },
    status: { 
        type: String, 
        enum: ["running","completed","failed","archived"], 
        default: "running" 
    },
    description: {
        type: String
    },
    tags: [{ type: String }],
    duration: {
        type: Number,
        required: true
    },
    logs: [{
        date: { type: Date, default: Date.now },
        event: { type: String },
        tag: { type: String }
    }],
    startedAt: { type: Date, default: Date.now },
    artifacts:  [{ type: Types.ObjectId, ref: "Artifact" }],
}, { timestamps: true });

runSchema.virtual("params", {
  ref: "Param",
  localField: "_id",
  foreignField: "runId"
});

runSchema.virtual("metrics", {
  ref: "Metric",
  localField: "_id",
  foreignField: "runId"
});

export default model("Run", runSchema);
