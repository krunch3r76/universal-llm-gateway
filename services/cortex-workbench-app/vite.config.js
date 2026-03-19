import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  base: "/workbench/",
  resolve: {
    alias: {
      "@workbench": path.resolve(__dirname, "../cortex-workbench"),
      react: path.resolve(__dirname, "node_modules/react"),
      "react-dom": path.resolve(__dirname, "node_modules/react-dom"),
    },
  },
  server: {
    host: "0.0.0.0",
    fs: {
      allow: [".."],
    },
  },
});
