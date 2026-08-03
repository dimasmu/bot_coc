import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8088",
        ws: true,
      },
      "/api": {
        target: "http://127.0.0.1:8088",
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
