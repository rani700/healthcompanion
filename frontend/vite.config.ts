import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API base is read from VITE_API_BASE at build/runtime (see src/api.ts),
// defaulting to the local FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
