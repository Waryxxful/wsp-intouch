import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { federation } from '@module-federation/vite';

export default defineConfig({
  plugins: [
    react(),
    federation({
      name: 'wsp_intouch',
      filename: 'remoteEntry.js',
      exposes: {
        './App': './src/App.tsx',
      },
      // @duralux/ui NUNCA va en shared -- design-system.md: "NO pongas @duralux/ui
      // en shared (desacopla deploys)". El theming cruzado shell<->remote no depende
      // de identidad de modulo/contexto de React: se hereda via clase CSS en <html>
      // (.app-skin-dark), asi que compartir la instancia del paquete no hace falta.
      shared: {
        react: { singleton: true, requiredVersion: '^18.0.0' },
        'react-dom': { singleton: true, requiredVersion: '^18.0.0' },
        'react-router-dom': { singleton: true, requiredVersion: '^6.0.0' },
      },
    }),
  ],
  build: {
    target: 'esnext',
    outDir: './dist',
    emptyOutDir: true,
  },
  server: {
    port: 8041,
    proxy: {
      '/intouch/api': { target: 'http://127.0.0.1:8040', changeOrigin: true },
    },
  },
});
