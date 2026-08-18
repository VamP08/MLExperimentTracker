import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Where the demo is served from.
 *
 * The demo is deployed as a Render static site, which serves from the root of its own
 * subdomain, so it needs no path prefix. Kept as a named constant because a host that
 * publishes under a subpath (GitHub Pages does: `https://<user>.github.io/<repo>/`) needs
 * this and the router's `basename` changed together, and they are easy to change apart.
 */
const DEMO_BASE = '/'

/** The `<title>` in index.html, and what the demo build replaces it with. */
const TITLE = '<title>MLExperimentTracker</title>'
const DEMO_TITLE = '<title>MLExperimentTracker — static demo</title>'

/**
 * The two things the demo build needs that the normal build must not have.
 *
 * 1. `404.html`. GitHub Pages has no SPA rewrite. A deep link such as
 *    `/MLExperimentTracker/runs/abc` is a request for a file that does not exist, and
 *    Pages answers it with its own 404 page — so the app never loads and the client
 *    router never gets the chance to handle the URL. Serving the app's own HTML as
 *    `404.html` hands the request back to the router instead. This is the reason the
 *    router's `basename` has to match `base`: the router receives the full path.
 *
 * 2. A title that says "demo". The banner in the page says it too, but the banner is
 *    dismissible and the tab label is not.
 *
 * Both are derived from the real build output rather than written by hand, so they cannot
 * drift from index.html.
 */
function demoBuild(): Plugin {
  return {
    name: 'mlexp-demo-build',
    apply: 'build',
    enforce: 'post',

    transformIndexHtml: {
      order: 'post',
      handler(html) {
        if (!html.includes(TITLE)) {
          throw new Error(
            `the demo build expected ${TITLE} in index.html and did not find it; ` +
              'update TITLE in vite.config.ts so the tab still says this is a demo',
          )
        }
        return html.replace(TITLE, DEMO_TITLE)
      },
    },

    generateBundle(_options, bundle) {
      const index = bundle['index.html']
      if (!index || index.type !== 'asset') {
        // Loudly, because the failure it prevents is invisible on the landing page and
        // hits every refresh of a sub-route.
        throw new Error('index.html was not emitted, so 404.html cannot be derived from it')
      }
      this.emitFile({ type: 'asset', fileName: '404.html', source: index.source })
    },
  }
}

/**
 * Path suffixes of the demo-only stylesheets, in the forward-slash form Rollup ids use.
 *
 * Two files: the banner's own rules, and the disabled states for the controls the demo
 * cannot operate. Both are reachable only from `DemoBanner.tsx`.
 */
const DEMO_ONLY_CSS = [
  '/components/DemoBanner/DemoBanner.css',
  '/demo/demoOnly.css',
]

/**
 * Empty the demo-only stylesheets in the normal build.
 *
 * App.tsx renders the banner behind `IS_DEMO`, which folds to `false` outside the demo, so
 * the component's JavaScript is tree-shaken away. Its `import './DemoBanner.css'` is a
 * side-effecting import, though, and side effects survive tree-shaking on principle — so
 * 1.5 kB of rules that can never match anything rode along in the stylesheet the wheel
 * ships. Rollup's `treeshake.moduleSideEffects` does not reach it: Vite accumulates CSS in
 * its own plugin, outside Rollup's tree-shaker. Emptying the files before Vite reads them
 * does, and it is narrow enough to be obviously safe — a fixed list, one build mode.
 *
 * This is also why a demo-only rule belongs in `src/demo/demoOnly.css` rather than beside
 * the component it styles: a `-disabled` rule added to `Logs.css` would ship in the wheel,
 * where nothing can ever match it.
 */
function dropDemoOnlyStyles(): Plugin {
  return {
    name: 'mlexp-drop-demo-only-styles',
    apply: 'build',
    enforce: 'pre',
    transform(_code, id) {
      const path = id.replace(/\\/g, '/')
      if (!DEMO_ONLY_CSS.some((suffix) => path.includes(suffix))) return null
      return { code: '', map: null }
    },
  }
}

// `npm run build:demo` runs `vite build --mode demo`, which loads `.env.demo` and with it
// VITE_DEMO=1. The mode is read here rather than `process.env` for two reasons: the flag
// then lives in exactly one file, and this config stays free of Node globals (the frontend
// has no @types/node, and `tsc -b` type-checks this file).
export default defineConfig(({ mode }) => {
  const isDemo = mode === 'demo'

  return {
    base: isDemo ? DEMO_BASE : '/',
    plugins: isDemo ? [react(), demoBuild()] : [react(), dropDemoOnlyStyles()],
    server: {
      // Force polling so HMR never goes quiet
      watch: {
        usePolling: true,
        interval: 100,      // check every 100 ms
      },
      hmr: {
        overlay: true       // still show runtime errors in the browser
      },
      proxy: {
        "/api": {
          target: "http://localhost:5000",
          changeOrigin: true,
          secure: false,
        },
      },
    },
  }
})
