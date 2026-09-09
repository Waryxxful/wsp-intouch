import { Routes, Route, Navigate } from 'react-router-dom';
import { useEffect } from 'react';
// Module Federation no inyecta el CSS de un remote expuesto en el host --
// el shell ya carga bootstrap/theme globales, pero estilos de componente
// especificos (ej. el margin-bottom de PageHeader que compensa el gutter
// negativo de la fila siguiente) solo llegan si el remote los importa el
// mismo, en su propio entry expuesto (mismo patron que call_reviews).
import '@duralux/ui/styles/grancrm-ui.css';
import type { GranCrmRemoteProps } from './types';
import { DashboardPage } from './pages/DashboardPage';
import { ChatOperatorPage } from './pages/ChatOperatorPage';
import { SettingsPage } from './pages/SettingsPage';
import { LeadsPage } from './pages/LeadsPage';
import { CampanasPage } from './pages/CampanasPage';
import { AgendamientosPage } from './pages/AgendamientosPage';

export default function App({ session, bus, basename }: GranCrmRemoteProps) {
  // El host pasa apiBase='/intouch/api' via el contract, pero los components
  // usan paths absolutos /intouch/api/... directamente — no llamamos
  // configureApi() para no generar doble prefijo (mismo patron que pompeyo).
  // Deuda pendiente: unificar los ~26 archivos de frontend/src que hardcodean
  // ese path absoluto para que lean el `apiBase` del contract en su lugar --
  // no se hizo ahora por ser un refactor amplio sin cobertura de tests.
  useEffect(() => {
    const handler = () => bus.emit('sessionExpired');
    window.addEventListener('grancrm:sessionExpired', handler);
    return () => window.removeEventListener('grancrm:sessionExpired', handler);
  }, [bus]);

  return (
    // "main-content" (padding:30px 30px 5px del theme compartido, ver
    // DESIGN.md linea 80/198/223) va DENTRO de cada pagina, envolviendo solo
    // el contenido posterior al <PageHeader> -- PageHeader ya trae sus
    // propios 30px de padding interno, así que si "main-content" envolviera
    // tambien al header quedarian sumados (60px), desalineando el titulo
    // respecto al contenido de abajo. Mismo patron que la app de referencia
    // (call_reviews/AppPage.tsx): PageHeader afuera, main.main-content
    // adentro envolviendo el resto.
    <div>
      <Routes>
        <Route index element={<DashboardPage basename={basename} />} />
        <Route path="chat" element={<ChatOperatorPage basename={basename} />} />
        <Route path="chat/:conversationId" element={<ChatOperatorPage basename={basename} />} />
        <Route path="leads" element={<LeadsPage />} />
        <Route path="campanas" element={<CampanasPage />} />
        <Route path="agendamientos" element={<AgendamientosPage />} />
        <Route path="settings/*" element={<SettingsPage session={session} bus={bus} basename={basename} />} />
        <Route path="*" element={<Navigate to="." replace />} />
      </Routes>
    </div>
  );
}
