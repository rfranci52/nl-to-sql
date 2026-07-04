import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The React app calls /api/*; in dev we proxy that to the FastAPI backend on
// :8000, so the fetch path is identical in dev and in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
