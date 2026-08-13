// Must be the first import: ESM evaluates imports before this module's body, so
// loading .env any later means fileSystem.service.js has already computed
// STORAGE_PATH from a process.env that does not have it yet.
import "dotenv/config";

import express from "express";
import cors from "cors";
import path from "path";
import { fileURLToPath } from "url";
import dashboardRoutes from "./routes/dashboard.routes.js";
import runsRoutes from "./routes/runs.routes.js";
import experimentRoutes from "./routes/experiment.routes.js";
import metricsRoutes from "./routes/metrics.routes.js";
// Imported so the startup banner reports the path the service actually resolved,
// rather than re-deriving it and printing something the service never used.
import fileSystemService from "./services/fileSystem.service.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();

// — Logging & middleware —
app.use((req, res, next) => {
  console.log(`${req.method} ${req.path}`);
  next();
});
// Vite's dev server proxies /api, so same-origin requests need no CORS entry at
// all. These cover a browser hitting the API directly during debugging.
app.use(cors({ origin: ["http://localhost:5173", "http://localhost:3000"] }));
app.use(express.json());

// — 1) MOUNT YOUR API ROUTES FIRST —
app.use("/api/dashboard", dashboardRoutes);
app.use("/api/run", runsRoutes);
app.use("/api/run", metricsRoutes); // Additional metrics routes
app.use("/api/experiment", experimentRoutes);
app.use("/api/experiments", experimentRoutes); // Alias for experiments list

// — 2) THEN, only in production, serve the React build + catch-all —
if (process.env.NODE_ENV === "production") {
  const clientDist = path.resolve(__dirname, "../ml_frontend/dist");
  app.use(express.static(clientDist));
  // Express 5 uses path-to-regexp 8, where a bare "*" is a syntax error.
  app.get("/*splat", (_req, res) => {
    res.sendFile(path.join(clientDist, "index.html"));
  });
}

// — 3) START the server (no MongoDB needed) —
// Bound to loopback deliberately. There is no authentication layer, by design:
// this is a single-user local tool, so the trust boundary is the loopback
// interface rather than a login form. Do not change HOST without adding one.
const HOST = process.env.HOST || "127.0.0.1";
const PORT = process.env.PORT || 5000;

console.log("Using local file system for experiment storage");
console.log(`Storage path: ${fileSystemService.storagePath}`);

app.listen(PORT, HOST, () =>
  console.log(`Server listening on http://${HOST}:${PORT}`)
);
