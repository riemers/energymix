import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// base "./" zodat alles relatief laadt achter HA Ingress
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  build: { outDir: "../app/energymix/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8099" } },
});
