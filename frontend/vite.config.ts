import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The page is served at http://localhost:5173 and calls the FastAPI backend directly at
// http://localhost:8000 (see src/api.ts). The backend allows this origin with CORS.
// strictPort keeps the origin fixed: if 5173 is busy, Vite stops instead of moving to
// another port the backend wouldn't recognise.
export default defineConfig({
  plugins: [react()],
  server: { host: 'localhost', port: 5173, strictPort: true },
})
