import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
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
}
});
