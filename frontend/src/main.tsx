// Punto de entrada solo para `vite dev`/`vite build` standalone — el shell
// GranCRM en produccion nunca ejecuta este archivo, monta ./App directo
// via Module Federation (loadRemote + remoteEntry.js).
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <div style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
        <h2>Bot Demo Remote (dev mode)</h2>
        <p>Este remote corre como Module Federation. Cargalo via el shell GranCRM.</p>
      </div>
    </StrictMode>
  );
}
