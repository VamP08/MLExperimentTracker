import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const TITLE = '<title>MLExperimentTracker</title>'
const DEMO_TITLE = '<title>MLExperimentTracker — static demo</title>'

// Link previews are demo-only; the image is hosted on GitHub so the wheel does not carry it.
const DEMO_URL = 'https://mlexperimenttracker-demo.onrender.com/'
const PREVIEW_IMAGE = 'https://raw.githubusercontent.com/VamP08/MLExperimentTracker/main/.github/social-preview.jpg'
const PREVIEW_TEXT = 'A local-first ML experiment tracker: runs, comparisons and reproducibility checks, shown from a snapshot of real runs.'
const LINK_PREVIEW = [
  `<meta property="og:type" content="website" />`,
  `<meta property="og:site_name" content="MLExperimentTracker" />`,
  `<meta property="og:title" content="MLExperimentTracker — experiment tracking you can verify" />`,
  `<meta property="og:description" content="${PREVIEW_TEXT}" />`,
  `<meta property="og:url" content="${DEMO_URL}" />`,
  `<meta property="og:image" content="${PREVIEW_IMAGE}" />`,
  `<meta property="og:image:width" content="1280" />`,
  `<meta property="og:image:height" content="640" />`,
  `<meta property="og:image:alt" content="MLExperimentTracker: experiment tracking you can verify" />`,
  `<meta name="twitter:card" content="summary_large_image" />`,
  `<meta name="theme-color" content="#07090d" />`,
].join('\n    ')

// The tab says "demo" because the banner can be dismissed and the tab label can't.
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
        return html.replace(TITLE, `${DEMO_TITLE}\n    ${LINK_PREVIEW}`)
      },
    },
  }
}

const DEMO_ONLY_CSS = [
  '/components/DemoBanner/DemoBanner.css',
  '/demo/demoOnly.css',
]

// CSS imports survive tree-shaking, so demo-only rules would otherwise ship in the wheel.
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

// The mode, not process.env: tsc checks this file and the frontend has no @types/node.
export default defineConfig(({ mode }) => {
  const isDemo = mode === 'demo'

  return {
    plugins: isDemo ? [react(), demoBuild()] : [react(), dropDemoOnlyStyles()],
    server: {
      // Force polling so HMR never goes quiet
      watch: {
        usePolling: true,
        interval: 100,
      },
      hmr: {
        overlay: true
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
