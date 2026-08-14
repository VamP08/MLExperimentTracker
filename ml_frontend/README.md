# ml_frontend

The dashboard source: a Vite + React + TypeScript SPA, plain per-component CSS, no UI
framework. It is not a standalone application — it reads the API served by the Python
package in `../src/mlexperimenttracker`, and `scripts/build_ui.py` builds it into the
wheel so `mlexp ui` serves the bundle from the installed package.

For what the project is, how to install it and how to run it, see the [root
README](../README.md).

## Working on the dashboard

```bash
npm install
npm run dev      # http://localhost:5173
```

`vite.config.ts` proxies `/api` to `http://localhost:5000`, which is where `mlexp ui`
binds by default, so start the API first in another shell:

```bash
mlexp ui
```

Every fetch in `src/` uses a relative `/api/...` URL so that the same code works behind
the proxy in development and behind the packaged server in production. Keep it that way.

```bash
npm run build    # tsc -b && vite build -> dist/
npx eslint .
```
