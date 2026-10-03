import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// при разработке API проксируется на локально запущенный сервис
const target = process.env.CATDETECT_API ?? "http://localhost:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target, ws: true, changeOrigin: true } },
  },
});
