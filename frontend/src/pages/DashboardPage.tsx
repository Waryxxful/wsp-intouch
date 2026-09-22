import { useEffect, useState } from 'react';
import { Card, StatsCard, Badge, Alert, ApexChart, ChartCard, PageHeader, Select, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';
import { useBreadcrumbLink } from '../nav/useBreadcrumbLink';

interface ChartPoint { date: string; count: number; }
interface AgentDist { active_agent: string; count: number; }
interface FunnelStage {
  stage: string;
  label: string;
  count: number;
  rate_step: number | null;
  rate_total: number | null;
}
interface Kpis {
  tiempo_respuesta_promedio_seg: number | null;
  tiempo_respuesta_p50_seg: number | null;
  pct_respondidas: number | null;
  pct_agendada: number | null;
  templates_enviados: number;
  fuera_de_horario: number;
}
interface AlertItem {
  severity: 'critica' | 'media' | 'baja';
  title: string;
  cause: string;
  action: string;
}
interface FunnelEvolutionPoint {
  date: string;
  conversaciones: number;
  respondidas: number;
  cita_agendada: number;
  recordatorio_enviado: number;
  recordatorio_confirmado: number;
}

interface DashboardData {
  es_proyeccion?: boolean;
  conv_count: number;
  msg_count: number;
  msg_today: number;
  msg_by_role: { user: number; assistant: number; human: number };
  active_24h: number;
  active_flows: number;
  chart: ChartPoint[];
  bot_on: boolean;
  connected: boolean;
  phone_id: string;
  model: string;
  reservas_total: number;
  reservas_today: number;
  reservas_week: number;
  human_mode_count: number;
  agent_distribution: AgentDist[];
  range: string;
  funnel: FunnelStage[];
  kpis: Kpis;
  funnel_evolution: FunnelEvolutionPoint[];
  alerts: AlertItem[];
}

// Meta Graph API stats — estadisticas del WhatsApp Business Account.
// Fuente: /intouch/api/admin/meta-stats y /intouch/api/admin/template-stats.
interface MetaStats {
  ok: boolean;
  start: string;
  end: string;
  total_messages: { delivered: number; sent: number | null; received: number | null };
  delivered_by_category: Record<string, number>;
  free_delivered: Record<string, number>;
  paid_delivered_by_category: Record<string, number>;
  total_cost_approx: number;
}
interface TemplateRow {
  id: string;
  name: string;
  status: string;
  category: string;
  language: string;
  sent: number;
  delivered: number;
  read: number;
  replied: number;
  clicked_by_button: Record<string, number>;
  delivery_rate_pct: number;
  read_rate_pct: number;
  reply_rate_pct: number;
}
interface TemplateStats {
  ok: boolean;
  start: string;
  end: string;
  templates: TemplateRow[];
  shared_waba: boolean;
}

// Labels legibles para las categorias de Meta.
const META_CATEGORY_LABELS: Record<string, string> = {
  MARKETING: 'Marketing',
  UTILITY: 'Utilidad',
  AUTHENTICATION: 'Autenticación',
  AUTHENTICATION_INTERNATIONAL: 'Autenticación internacional',
  SERVICE: 'Servicio',
  UNKNOWN: 'Otras',
};
const META_FREE_LABELS: Record<string, string> = {
  FREE_CUSTOMER_SERVICE: 'Atención al cliente gratuita',
  FREE_ENTRY_POINT: 'Punto de acceso gratuito',
};

// Default: ultimos 30 dias (hasta hoy). YYYY-MM-DD.
function defaultMetaRange(): { start: string; end: string } {
  const today = new Date();
  const past = new Date(today);
  past.setDate(past.getDate() - 30);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(past), end: fmt(today) };
}

const RANGE_OPTIONS = [
  { value: '7d', label: 'Últimos 7 días' },
  { value: 'today', label: 'Hoy' },
  { value: 'month', label: 'Mes actual' },
  { value: 'prev_month', label: 'Mes anterior' },
];

const AGENT_LABELS: Record<string, string> = {
  agendamiento: 'Agendamiento',
  confirmacion: 'Confirmación de recordatorio',
  faq: 'Preguntas libres (FAQ)',
};

const FUNNEL_COLORS = ['#1d2f7a', '#253f9e', '#3454d1', '#6b85e0', '#93a6e8'];
// Paleta categorica (no la rampa ordinal del embudo) para el grafico de evolucion:
// 5 lineas simultaneas que pueden cruzarse necesitan colores bien distintos entre si,
// no tonos del mismo hue. Colores de marca duralux-ui, validados con el skill dataviz
// (--mode light, sin --ordinal): blue/orange/cyan/purple/green.
const EVOLUTION_COLORS = ['#3454d1', '#c2410c', '#3dc7be', '#6f42c1', '#17c666'];

// Piso visual para el embudo tipo 'trapezoid': una etapa en 0 tapa hacia un punto
// dentro de una sola fila si la etapa anterior tiene datos, dejando un triangulo
// brusco. Se reemplaza el 0 por un pequeno porcentaje del maximo (nunca los datos
// reales, que se siguen mostrando en el label via `data.funnel`, no via `val`).
function funnelDisplayData(stages: FunnelStage[]): number[] {
  const maxCount = Math.max(...stages.map(s => s.count), 1);
  const floor = Math.max(1, Math.round(maxCount * 0.05));
  return stages.map(s => (s.count > 0 ? s.count : floor));
}

export function DashboardPage({ basename }: { basename: string }) {
  // Breadcrumb a la raíz del remoto sin recargar el shell (ver useBreadcrumbLink).
  const inicio = useBreadcrumbLink(basename);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState('7d');

  // Meta stats: rango + datos + loading state independiente para no bloquear
  // el resto del dashboard cuando Meta esta lento o falla.
  // Vista de proyeccion (docx S19). Arranca SIEMPRE en datos reales: si
  // arrancara en proyeccion, alguien podria abrir el panel un dia cualquiera y
  // leer cifras inventadas creyendo que son medidas.
  const [modoDemo, setModoDemo] = useState(false);
  const initialMetaRange = defaultMetaRange();
  const [metaStart, setMetaStart] = useState<string>(initialMetaRange.start);
  const [metaEnd, setMetaEnd] = useState<string>(initialMetaRange.end);
  const [metaStats, setMetaStats] = useState<MetaStats | null>(null);
  const [templateStats, setTemplateStats] = useState<TemplateStats | null>(null);
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaError, setMetaError] = useState<string>('');

  useEffect(() => {
    const qs = modoDemo ? 'modo=demo' : `range=${range}`;
    const load = () =>
      apiFetch<DashboardData>(`/intouch/api/admin/dashboard?${qs}`)
        .then(setData).catch(console.error).finally(() => setLoading(false));
    load();
    // La proyeccion es estatica: refrescarla cada 30s solo genera trafico.
    if (modoDemo) return;
    const i = setInterval(load, 30000);
    return () => clearInterval(i);
  }, [range, modoDemo]);

  useEffect(() => {
    if (!metaStart || !metaEnd) return;
    setMetaLoading(true);
    setMetaError('');
    const qs = new URLSearchParams({ start: metaStart, end: metaEnd }).toString();
    Promise.all([
      apiFetch<MetaStats>(`/intouch/api/admin/meta-stats?${qs}`),
      apiFetch<TemplateStats>(`/intouch/api/admin/template-stats?${qs}`),
    ])
      .then(([m, t]) => {
        setMetaStats(m);
        setTemplateStats(t);
      })
      .catch((e) => {
        setMetaError(e?.message || 'Error consultando Meta');
      })
      .finally(() => setMetaLoading(false));
  }, [metaStart, metaEnd]);

  if (loading) return <LoadingState message="Cargando dashboard..." />;
  if (!data) return <Alert variant="danger" title="Error">No se pudo cargar el dashboard.</Alert>;

  const totalAgentes = data.agent_distribution.reduce((a, b) => a + b.count, 0);

  return (
    <div>
      {data.es_proyeccion && (
        <Alert variant="warning" icon="feather-alert-triangle" title="Vista de proyección">
          Estas cifras son de demostración, no mediciones reales de este bot.
          Desactivá “Proyección demo” para ver los datos efectivos.
        </Alert>
      )}
      <PageHeader
        title="Asesor Comercial IA — Dashboard"
        breadcrumbs={[{ label: 'Inicio', ...inicio }, { label: 'Dashboard' }]}
      >
        <div className="form-check form-switch me-3">
          <input
            className="form-check-input"
            type="checkbox"
            role="switch"
            id="toggle-proyeccion"
            checked={modoDemo}
            onChange={(e) => { setLoading(true); setModoDemo(e.target.checked); }}
          />
          <label className="form-check-label" htmlFor="toggle-proyeccion">
            Proyección demo
          </label>
        </div>
        {/* Los 3-4 indicadores de estado agrupados en una sola caja (borde +
            fondo propio) en vez de badges sueltos flotando en el header --
            se leen como un panel de estado, no como texto suelto. */}
        <div className="d-flex align-items-center gap-2 border rounded-3 bg-gray-100 px-3 py-2">
          <Badge variant={data.bot_on ? 'success' : 'danger'} soft>
            <i className="feather-power me-1"></i>Bot {data.bot_on ? 'activo' : 'apagado'}
          </Badge>
          <Badge variant={data.connected ? 'success' : 'warning'} soft>
            <i className="feather-wifi me-1"></i>
            {data.connected ? 'WhatsApp conectado' : 'WhatsApp sin configurar'}
          </Badge>
          <Badge variant="info" soft>
            <i className="feather-cpu me-1"></i>{data.model}
          </Badge>
          {data.human_mode_count > 0 && (
            <Badge variant="warning">
              <i className="feather-user-check me-1"></i>{data.human_mode_count} en modo HUMAN
            </Badge>
          )}
        </div>
      </PageHeader>

      {/* main-content (30px/30px/5px, theme compartido) va aca -- PageHeader
          queda afuera, mismo patron que call_reviews/AppPage.tsx (PageHeader
          ya trae sus propios 30px, sumarlo con el de main-content desalinea
          el titulo respecto al contenido de abajo). */}
      <main className="main-content">
      {/* gx-3 (no gy) + mt-4: "row g-3" aplica margin-top negativo (gutter
          vertical de Bootstrap) que, al ser la primera fila justo debajo de
          PageHeader (z-index alto, sticky), quedaba tapada por el header en
          vez de absorber el margen en el elemento anterior. gx-3 saca ese
          margen negativo, pero PageHeader no aporta padding-bottom propio
          (medido: 0px) -- sin mt-4 la fila queda pegada al header con 0px de
          aire. mt-4 = 24px, el "ritmo vertical uniforme" que documenta
          DESIGN.md (linea 223/272) para todo el contenido bajo el header. */}
      <div className="row gx-3 mb-4 mt-4">
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-check-circle"
            iconBg="bg-soft-success text-success"
            value={String(data.reservas_total)}
            label="Reservas activas"
            trend={
              data.reservas_today > 0
                ? { value: `+${data.reservas_today} hoy`, up: true }
                : data.reservas_week > 0
                  ? { value: `+${data.reservas_week} 7d`, up: true }
                  : undefined
            }
          />
        </div>
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-activity"
            iconBg="bg-soft-primary text-primary"
            value={String(data.active_flows)}
            label="Conversaciones"
            trend={{ value: `${data.active_24h} en 24h`, up: data.active_24h > 0 }}
          />
        </div>
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-message-circle"
            iconBg="bg-soft-info text-info"
            value={String(data.msg_today)}
            label="Mensajes hoy"
            trend={{
              value: `↓${data.msg_by_role.user} ↑${data.msg_by_role.assistant}${data.msg_by_role.human ? ` 🧑${data.msg_by_role.human}` : ''}`,
              up: true,
            }}
          />
        </div>
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-database"
            iconBg="bg-soft-warning text-warning"
            value={data.msg_count.toLocaleString('es-CL')}
            label="Mensajes totales"
          />
        </div>
      </div>

      <div className="row g-3 mb-4">
        <div className="col-lg-7">
          <ChartCard title="Mensajes últimos 7 días" subtitle="Volumen diario">
            <ApexChart
              type="area"
              height={250}
              options={{
                colors: ['#3454d1'],
                stroke: { curve: 'smooth', width: 2 },
                fill: { type: 'gradient', gradient: { opacityFrom: 0.4, opacityTo: 0 } },
                xaxis: {
                  categories: data.chart.map(p => {
                    const d = new Date(p.date);
                    return d.toLocaleDateString('es-CL', { weekday: 'short', day: '2-digit' });
                  }),
                },
                dataLabels: { enabled: false },
                grid: { borderColor: 'rgba(128,128,128,0.15)' },
                chart: { toolbar: { show: false } },
                tooltip: { x: { show: true } },
              }}
              series={[{ name: 'Mensajes', data: data.chart.map(p => p.count) }]}
            />
          </ChartCard>
        </div>

        <div className="col-lg-5">
          <ChartCard title="Especialista activo" subtitle="Conversaciones por especialista">
            {totalAgentes > 0 ? (
              <ApexChart
                type="donut"
                height={250}
                options={{
                  labels: data.agent_distribution.map(a => AGENT_LABELS[a.active_agent] || a.active_agent),
                  colors: ['#3454d1', '#17c666', '#ffa21d'],
                  legend: { position: 'bottom' },
                  dataLabels: { enabled: true, formatter: (val: number) => `${Math.round(val)}%` },
                  plotOptions: {
                    pie: { donut: { labels: { show: true, total: { show: true, label: 'Total', formatter: () => String(totalAgentes) } } } },
                  },
                }}
                series={data.agent_distribution.map(a => a.count)}
              />
            ) : (
              <div className="text-body-secondary fs-13 py-4 text-center">
                Sin conversaciones activas todavía.
              </div>
            )}
          </ChartCard>
        </div>
      </div>

      <div className="d-flex justify-content-between align-items-center mb-3">
        <h5 className="mb-0">Flujo de leads (WhatsApp)</h5>
        <div style={{ maxWidth: 220 }}>
          <Select
            options={RANGE_OPTIONS}
            value={range}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setRange(e.target.value)}
          />
        </div>
      </div>

      <div className="row g-3 mb-4">
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-clock"
            iconBg="bg-soft-info text-info"
            value={data.kpis.tiempo_respuesta_promedio_seg !== null ? `${Math.round(data.kpis.tiempo_respuesta_promedio_seg / 60)} min` : '—'}
            label="Respuesta"
          />
        </div>
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-percent"
            iconBg="bg-soft-success text-success"
            value={data.kpis.pct_agendada !== null ? `${Math.round(data.kpis.pct_agendada * 100)}%` : '—'}
            label="Cita agendada"
          />
        </div>
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-send"
            iconBg="bg-soft-primary text-primary"
            value={String(data.kpis.templates_enviados)}
            label="Templates"
          />
        </div>
        <div className="col-md-3 col-sm-6">
          <StatsCard
            icon="feather-moon"
            iconBg="bg-soft-warning text-warning"
            value={String(data.kpis.fuera_de_horario)}
            label="Fuera de horario"
          />
        </div>
      </div>

      <div className="row g-3 mb-4">
        <div className="col-lg-8">
          <ChartCard title="Embudo" subtitle="Conversión etapa a etapa">
            <div className="d-flex align-items-center">
              <div style={{ width: 220, flexShrink: 0 }}>
                <ApexChart
                  type="funnel"
                  height={220}
                  options={{
                    colors: FUNNEL_COLORS,
                    chart: { toolbar: { show: false } },
                    plotOptions: {
                      bar: { distributed: true, borderRadiusApplication: 'around', borderRadius: 4 },
                      // 'trapezoid' + 'taper': lados con pendiente continua entre etapas,
                      // terminando en punta. Cada etapa afina su ancho hacia el valor de la
                      // SIGUIENTE, asi que con una etapa en 0 el afinado colapsaria de golpe
                      // dentro de una sola fila (triangulo grande y brusco). Se lo suaviza con
                      // un piso visual minimo (`funnelDisplayData`) en vez de cambiar de shape.
                      funnel: { shape: 'trapezoid', lastShape: 'taper' },
                    },
                    fill: {
                      type: 'gradient',
                      gradient: { shade: 'light', type: 'vertical', shadeIntensity: 0.35, opacityFrom: 1, opacityTo: 0.9 },
                    },
                    // Sin dataLabels adentro de las bandas: con el piso visual minimo, las
                    // bandas de las etapas en 0 quedan muy angostas y el texto se corta contra
                    // el borde. El detalle de cada etapa se muestra aparte, en texto plano
                    // (columna de la derecha) que nunca se recorta sea cual sea el ancho.
                    dataLabels: { enabled: false },
                    xaxis: { categories: data.funnel.map(s => s.label) },
                    legend: { show: false },
                    tooltip: { enabled: false },
                  }}
                  series={[{ name: 'Etapa', data: funnelDisplayData(data.funnel) }]}
                />
              </div>
              <div className="flex-grow-1 ps-4">
                {data.funnel.map((stage, i) => {
                  const max = data.funnel[0]?.count || 1;
                  const pct = Math.round((stage.count / max) * 100);
                  return (
                    <div key={stage.stage} className="mb-3">
                      <div className="d-flex justify-content-between fs-13 mb-1">
                        <span>{stage.label}</span>
                        <span className="fw-semibold">
                          {stage.count}
                          {stage.rate_total !== null && ` · ${Math.round(stage.rate_total * 100)}%`}
                        </span>
                      </div>
                      <div className="progress" style={{ height: 6 }}>
                        <div
                          className="progress-bar"
                          style={{ width: `${pct}%`, backgroundColor: FUNNEL_COLORS[i % FUNNEL_COLORS.length] }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </ChartCard>
        </div>

        <div className="col-lg-4">
          <Card title="Alertas inteligentes" stretch bodyClassName="d-flex flex-column gap-3">
            {data.alerts.length > 0 ? (
              data.alerts.map((a, i) => {
                const isCritica = a.severity === 'critica';
                return (
                  <div
                    key={i}
                    className={`d-flex align-items-start gap-2 rounded-4 p-3 ${isCritica ? 'bg-soft-danger' : 'bg-soft-warning'}`}
                  >
                    <div
                      className={`rounded-circle d-flex align-items-center justify-content-center flex-shrink-0 bg-white ${isCritica ? 'text-danger' : 'text-warning'}`}
                      style={{ width: 36, height: 36 }}
                    >
                      <i className={isCritica ? 'feather-alert-triangle' : 'feather-alert-circle'}></i>
                    </div>
                    <div>
                      <div className="fw-semibold fs-13">{a.title}</div>
                      <div className="fs-12 text-body-secondary">{a.cause} — {a.action}</div>
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="flex-grow-1 d-flex flex-column align-items-center justify-content-center text-body-secondary fs-13">
                <i className="feather-check-circle d-block mb-2" style={{ fontSize: 24 }}></i>
                Sin alertas activas
              </div>
            )}
          </Card>
        </div>
      </div>

      {data.funnel_evolution.length > 0 && (
        <div className="row g-3 mb-4">
          <div className="col-md-12">
            <ChartCard title="Evolución del flujo de leads" subtitle="Conteo diario por etapa">
              <ApexChart
                type="bar"
                height={300}
                options={{
                  colors: EVOLUTION_COLORS,
                  plotOptions: { bar: { columnWidth: '70%', borderRadius: 3 } },
                  xaxis: {
                    categories: data.funnel_evolution.map(p => {
                      const d = new Date(p.date);
                      return d.toLocaleDateString('es-CL', { day: '2-digit', month: 'short' });
                    }),
                  },
                  dataLabels: { enabled: false },
                  grid: { borderColor: 'rgba(128,128,128,0.15)' },
                  chart: { toolbar: { show: false } },
                  legend: { position: 'bottom' },
                  tooltip: { shared: true, intersect: false },
                }}
                series={[
                  { name: 'Conversaciones', data: data.funnel_evolution.map(p => p.conversaciones) },
                  { name: 'Respondidas', data: data.funnel_evolution.map(p => p.respondidas) },
                  { name: 'Cita agendada', data: data.funnel_evolution.map(p => p.cita_agendada) },
                  { name: 'Recordatorio enviado', data: data.funnel_evolution.map(p => p.recordatorio_enviado) },
                  { name: 'Recordatorio confirmado', data: data.funnel_evolution.map(p => p.recordatorio_confirmado) },
                ]}
              />
            </ChartCard>
          </div>
        </div>
      )}

      {/* ═══ Estadisticas Meta (WhatsApp Business Account) ═══ */}
      <div className="d-flex align-items-center justify-content-between mb-3 mt-5">
        <div>
          <h5 className="mb-1">
            <i className="feather-bar-chart-2 me-2"></i>Estadísticas WhatsApp (Meta)
          </h5>
          <div className="fs-12 text-body-secondary">
            Datos directos del WhatsApp Business Account. Actualiza con el rango de fechas.
          </div>
        </div>
        <div className="d-flex align-items-center gap-2">
          <label className="fs-12 text-body-secondary mb-0">Desde</label>
          <input
            type="date"
            className="form-control form-control-sm"
            value={metaStart}
            onChange={(e) => setMetaStart(e.target.value)}
            style={{ maxWidth: 150, colorScheme: 'light dark' }}
          />
          <label className="fs-12 text-body-secondary mb-0">Hasta</label>
          <input
            type="date"
            className="form-control form-control-sm"
            value={metaEnd}
            onChange={(e) => setMetaEnd(e.target.value)}
            style={{ maxWidth: 150, colorScheme: 'light dark' }}
          />
        </div>
      </div>

      {metaError && (
        <Alert variant="warning" title="Aviso">
          No se pudieron cargar los datos de Meta: {metaError}. Revisá el token y los permisos.
        </Alert>
      )}

      {metaLoading && !metaStats && (
        <div className="text-center py-4 text-body-secondary fs-13">
          <i className="feather-loader me-2"></i>Cargando datos de Meta...
        </div>
      )}

      {metaStats && (
        <div className="row g-3 mb-4">
          {/* Card 1: Todos los mensajes */}
          <div className="col-lg-3 col-md-6">
            <Card title="Todos los mensajes">
              <div className="d-flex flex-column gap-2 fs-13">
                <div className="d-flex justify-content-between">
                  <span className="text-body-secondary">Entregados</span>
                  <span className="fw-semibold">
                    {metaStats.total_messages.delivered.toLocaleString('es-CL')}
                  </span>
                </div>
                {metaStats.total_messages.sent !== null && (
                  <div className="d-flex justify-content-between">
                    <span className="text-body-secondary">Enviados</span>
                    <span className="fw-semibold">
                      {metaStats.total_messages.sent.toLocaleString('es-CL')}
                    </span>
                  </div>
                )}
                {metaStats.total_messages.received !== null && (
                  <div className="d-flex justify-content-between">
                    <span className="text-body-secondary">Recibidos</span>
                    <span className="fw-semibold">
                      {metaStats.total_messages.received.toLocaleString('es-CL')}
                    </span>
                  </div>
                )}
              </div>
            </Card>
          </div>

          {/* Card 2: Entregados por categoria */}
          <div className="col-lg-3 col-md-6">
            <Card title="Mensajes entregados">
              <div className="d-flex flex-column gap-2 fs-13">
                {Object.entries(metaStats.delivered_by_category).length === 0 ? (
                  <span className="text-body-secondary">Sin datos.</span>
                ) : (
                  Object.entries(metaStats.delivered_by_category)
                    .sort((a, b) => b[1] - a[1])
                    .map(([cat, val]) => (
                      <div key={cat} className="d-flex justify-content-between">
                        <span className="text-body-secondary">
                          {META_CATEGORY_LABELS[cat] || cat}
                        </span>
                        <span className="fw-semibold">{val.toLocaleString('es-CL')}</span>
                      </div>
                    ))
                )}
              </div>
            </Card>
          </div>

          {/* Card 3: Gratuitos */}
          <div className="col-lg-3 col-md-6">
            <Card title="Mensajes gratuitos entregados">
              <div className="d-flex flex-column gap-2 fs-13">
                {Object.entries(metaStats.free_delivered).length === 0 ? (
                  <span className="text-body-secondary">Sin datos.</span>
                ) : (
                  Object.entries(metaStats.free_delivered)
                    .sort((a, b) => b[1] - a[1])
                    .map(([ptype, val]) => (
                      <div key={ptype} className="d-flex justify-content-between">
                        <span className="text-body-secondary">
                          {META_FREE_LABELS[ptype] || ptype}
                        </span>
                        <span className="fw-semibold">{val.toLocaleString('es-CL')}</span>
                      </div>
                    ))
                )}
              </div>
            </Card>
          </div>

          {/* Card 4: Pagados por categoria */}
          <div className="col-lg-3 col-md-6">
            <Card title="Mensajes pagados entregados">
              <div className="d-flex flex-column gap-2 fs-13">
                {Object.entries(metaStats.paid_delivered_by_category).length === 0 ? (
                  <span className="text-body-secondary">Sin datos.</span>
                ) : (
                  Object.entries(metaStats.paid_delivered_by_category)
                    .sort((a, b) => b[1] - a[1])
                    .map(([cat, val]) => (
                      <div key={cat} className="d-flex justify-content-between">
                        <span className="text-body-secondary">
                          {META_CATEGORY_LABELS[cat] || cat}
                        </span>
                        <span className="fw-semibold">{val.toLocaleString('es-CL')}</span>
                      </div>
                    ))
                )}
              </div>
            </Card>
          </div>
        </div>
      )}

      {templateStats && templateStats.shared_waba && (
        <Alert variant="info" title="Cuenta business compartida">
          Esta cuenta de WhatsApp Business tiene más de un número registrado
          (este bot comparte el WABA con otros). Meta no permite filtrar
          las plantillas por número, así que la tabla de abajo puede incluir
          templates y envíos de otro número de la misma cuenta.
        </Alert>
      )}

      {templateStats && templateStats.templates.length > 0 && (
        <div className="row g-3 mb-4">
          <div className="col-12">
            <Card
              title="Rendimiento por template"
              actions={
                <Badge variant="primary" soft>
                  {templateStats.templates.length} templates
                </Badge>
              }
            >
              <div className="table-responsive">
                <table className="table table-sm align-middle fs-13 mb-0">
                  <thead className="text-body-secondary">
                    <tr>
                      <th>Template</th>
                      <th className="text-end">Enviados</th>
                      <th className="text-end">Recibieron</th>
                      <th className="text-end">Vieron</th>
                      <th className="text-end">Respondieron</th>
                      <th className="text-end">% Lectura</th>
                      <th className="text-end">% Respuesta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {templateStats.templates.map((t) => (
                      <tr key={t.id}>
                        <td>
                          <div className="d-flex align-items-center gap-2">
                            <span className="fw-semibold text-body">{t.name}</span>
                            <Badge
                              variant={t.category === 'MARKETING' ? 'warning' : 'info'}
                              soft
                            >
                              {META_CATEGORY_LABELS[t.category] || t.category}
                            </Badge>
                          </div>
                          {Object.keys(t.clicked_by_button).length > 0 && (
                            <div className="fs-11 text-body-secondary mt-1">
                              {Object.entries(t.clicked_by_button)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join(' · ')}
                            </div>
                          )}
                        </td>
                        <td className="text-end">{t.sent.toLocaleString('es-CL')}</td>
                        <td className="text-end">{t.delivered.toLocaleString('es-CL')}</td>
                        <td className="text-end">{t.read.toLocaleString('es-CL')}</td>
                        <td className="text-end">{t.replied.toLocaleString('es-CL')}</td>
                        <td className="text-end">
                          <Badge
                            variant={
                              t.read_rate_pct >= 70
                                ? 'success'
                                : t.read_rate_pct >= 40
                                ? 'warning'
                                : 'danger'
                            }
                            soft
                          >
                            {t.read_rate_pct.toFixed(1)}%
                          </Badge>
                        </td>
                        <td className="text-end">
                          <Badge
                            variant={
                              t.reply_rate_pct >= 40
                                ? 'success'
                                : t.reply_rate_pct >= 20
                                ? 'warning'
                                : 'secondary'
                            }
                            soft
                          >
                            {t.reply_rate_pct.toFixed(1)}%
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </div>
      )}

      <div className="text-end fs-11 text-body-secondary">
        <i className="feather-refresh-cw me-1"></i>Auto-refresh cada 30s
      </div>
      </main>
    </div>
  );
}
