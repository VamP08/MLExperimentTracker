import mongoose from "mongoose";

const { Schema, model, Types } = mongoose;

const paramSchema = new Schema({
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
  type: {
    type: String,
    enum: ["number", "string", "boolean", "json"],
    default: "string"
  }
}, { timestamps: true });

export default model("Param", paramSchema);
