import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { PageHeader } from '@duralux/ui';
import type { GranCrmSession, EventBus } from '../types';
import { BotStatePanel } from '../panels/BotStatePanel';
import { ClienteActivoPanel } from '../panels/ClienteActivoPanel';
import { PromptPanel } from '../panels/PromptPanel';
import { LlmConfigPanel } from '../panels/LlmConfigPanel';
import { MediaLlmConfigPanel } from '../panels/MediaLlmConfigPanel';
import { ScrapingConfigPanel } from '../panels/ScrapingConfigPanel';
import { ScrapingLlmConfigPanel } from '../panels/ScrapingLlmConfigPanel';
import { WelcomePanel } from '../panels/WelcomePanel';
import { VacationPanel } from '../panels/VacationPanel';
import { BusinessHoursPanel } from '../panels/BusinessHoursPanel';
import { QuickResponsesPanel } from '../panels/QuickResponsesPanel';
import { SnippetsPanel } from '../panels/SnippetsPanel';
import { FiltersPanel } from '../panels/FiltersPanel';
import { HandoffPanel } from '../panels/HandoffPanel';
import { LogsPanel } from '../panels/LogsPanel';
import { AuditPanel } from '../panels/AuditPanel';
import { TestScenariosPanel } from '../panels/TestScenariosPanel';
import { EncuestasPanel } from '../panels/EncuestasPanel';
import { useBreadcrumbLink } from '../nav/useBreadcrumbLink';

interface Props { session: GranCrmSession; bus: EventBus; basename: string; }

const settingsNav = [
  { path: 'bot', label: 'Estado del bot', icon: 'feather-toggle-right' },
  { path: 'prompt', label: 'Prompt por especialista', icon: 'feather-cpu' },
  { path: 'tester', label: 'Tester', icon: 'feather-check-square' },
  { path: 'llm', label: 'Modelo & API key', icon: 'feather-key' },
  { path: 'scraping', label: 'Fuente de conocimiento (scraping)', icon: 'feather-globe' },
  { path: 'welcome', label: 'Bienvenida', icon: 'feather-message-circle' },
  { path: 'vacation', label: 'Fuera de horario', icon: 'feather-moon' },
  { path: 'hours', label: 'Horario de atención', icon: 'feather-clock' },
  { path: 'encuestas', label: 'Encuestas de satisfacción', icon: 'feather-clipboard' },
  { path: 'quick-responses', label: 'Respuestas rápidas', icon: 'feather-zap' },
  { path: 'snippets', label: 'Plantillas', icon: 'feather-file-text' },
  { path: 'filters', label: 'Filtros', icon: 'feather-filter' },
  { path: 'handoff', label: 'Handoff', icon: 'feather-user-check' },
  { path: 'logs', label: 'Logs', icon: 'feather-list' },
  { path: 'audit', label: 'Auditoría', icon: 'feather-shield' },
];

export function SettingsPage({ session, basename }: Props) {
  // Breadcrumb a la raíz del remoto sin recargar el shell (ver useBreadcrumbLink).
  const inicio = useBreadcrumbLink(basename);
  return (
    <>
      <PageHeader title="Configuración" breadcrumbs={[{ label: 'Inicio', ...inicio }, { label: 'Configuración' }]} />
      {/* main-content afuera del PageHeader, mismo motivo que DashboardPage --
          PageHeader ya trae 30px de padding propio. gx-3 (no gy) + mt-4:
          "row g-3" justo debajo de PageHeader (z-index alto) queda tapado por
          el gutter vertical negativo de Bootstrap; mt-4 = 24px, el ritmo
          vertical que documenta DESIGN.md. */}
      <main className="main-content">
      <div className="row gx-3 mt-4">
        <div className="col-lg-3">
          <div className="card">
            <div className="card-body p-3">
              <nav className="nav flex-column gap-1">
                {settingsNav.map(item => (
                  <NavLink
                    key={item.path} to={item.path}
                    className={({ isActive }) => `nav-link d-flex align-items-center gap-3 py-2 px-2 rounded ${isActive ? 'bg-soft-primary text-primary fw-semibold' : 'text-body'}`}
                  >
                    {({ isActive }) => (
                      <>
                        <span className={`avatar-text avatar-sm flex-shrink-0 ${isActive ? 'bg-primary text-white' : 'bg-gray-100 text-body-secondary'}`}>
                          <i className={item.icon}></i>
                        </span>
                        <span className="fs-13 text-truncate">{item.label}</span>
                      </>
                    )}
                  </NavLink>
                ))}
              </nav>
            </div>
          </div>
        </div>
        <div className="col-lg-9">
          <Routes>
            <Route index element={<Navigate to="bot" replace />} />
            <Route path="bot" element={<><BotStatePanel /><ClienteActivoPanel /></>} />
            <Route path="prompt" element={<PromptPanel />} />
            <Route path="tester" element={<TestScenariosPanel />} />
            <Route path="llm" element={<><LlmConfigPanel /><MediaLlmConfigPanel /></>} />
            <Route path="scraping" element={<><ScrapingLlmConfigPanel /><ScrapingConfigPanel /></>} />
            <Route path="welcome" element={<WelcomePanel />} />
            <Route path="vacation" element={<VacationPanel />} />
            <Route path="hours" element={<BusinessHoursPanel />} />
            <Route path="encuestas" element={<EncuestasPanel />} />
            <Route path="quick-responses" element={<QuickResponsesPanel />} />
            <Route path="snippets" element={<SnippetsPanel />} />
            <Route path="filters" element={<FiltersPanel />} />
            <Route path="handoff" element={<HandoffPanel />} />
            <Route path="logs" element={<LogsPanel />} />
            <Route path="audit" element={<AuditPanel />} />
          </Routes>
        </div>
      </div>
      </main>
    </>
  );
}
