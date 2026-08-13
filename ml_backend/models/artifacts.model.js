import mongoose from "mongoose";

const { Schema, model, Types } = mongoose;

const artifactSchema = new Schema({
    run: { 
        type: Types.ObjectId, 
        ref: "Run", 
        required: true 
    },
    name: { 
        type: String, 
        required: true 
    },
    type: String,                   // e.g. "model", "plot", "dataset"
    url: { 
        type: String, 
        required: true 
    },
    metadata: Schema.Types.Mixed,       // extra info (size, format…)

}, { timestamps: true });

export default model("Artifact", artifactSchema);
