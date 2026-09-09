import { useEffect, useMemo, useState } from 'react';
import { Card, PageHeader, Badge, DataTable, Modal, Input, Select, StatsCard, LoadingState, Alert } from '@duralux/ui';
import type { BadgeProps, DataTableColumn } from '@duralux/ui';
import { apiFetch } from '../api';

interface Lead {
  id: number;
  conversation_id: number | null;
  nombre: string;
  telefono: string;
  email: string;
  comuna: string;
  vehiculo_interes: string;
  vehiculo_codigo: string;
  presupuesto: number | null;
  pie_disponible: number | null;
  cuota_objetivo: number | null;
  plazo_compra: string;
  tiene_parte_pago: boolean | null;
  vehiculo_actual: string;
  intencion: string;
  sentimiento: string;
  urgencia: string;
  temperatura: string;
  lead_score: number;
  proxima_accion: string;
  resumen: string;
  ultima_interaccion: string | null;
  created_at: string;
}

type VarianteBadge = NonNullable<BadgeProps['variant']>;

const VARIANTE_TEMPERATURA: Record<string, VarianteBadge> = {
  HOT: 'danger',
  WARM: 'warning',
  COLD: 'info',
};

// Un monto vacio se muestra como raya, no como "$0": son cosas distintas
// (todavia no lo dijo / dijo que no tiene) y confundirlas en una pantalla
// comercial hace que un ejecutivo llame con el dato equivocado.
function pesos(valor: number | null): string {
  if (valor === null || valor === undefined) return '—';
  return `$${valor.toLocaleString('es-CL')}`;
}

function fecha(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('es-CL', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function LeadsPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [temperatura, setTemperatura] = useState('');
  const [busqueda, setBusqueda] = useState('');
  const [seleccionado, setSeleccionado] = useState<Lead | null>(null);
  const [modoDemo, setModoDemo] = useState(false);

  useEffect(() => {
    // Debounce + descarte de respuestas viejas. Sin esto sale un request por
    // tecla, y como las respuestas pueden llegar desordenadas, escribir
    // "spo" rapido dejaba la tabla mostrando el resultado de "sp".
    let vigente = true;
    const timer = setTimeout(() => {
      const params = new URLSearchParams();
      if (temperatura) params.set('temperatura', temperatura);
      if (busqueda.trim()) params.set('q', busqueda.trim());
      if (modoDemo) params.set('modo', 'demo');
      setLoading(true);
      setError('');
      apiFetch<Lead[]>(`/intouch/api/admin/leads?${params}`)
        .then(datos => { if (vigente) setLeads(datos); })
        .catch(err => { if (vigente) setError(err.message || 'No se pudieron cargar los leads.'); })
        .finally(() => { if (vigente) setLoading(false); });
    }, 300);
    return () => { vigente = false; clearTimeout(timer); };
  }, [temperatura, busqueda, modoDemo]);

  const totales = useMemo(() => ({
    hot: leads.filter(l => l.temperatura === 'HOT').length,
    warm: leads.filter(l => l.temperatura === 'WARM').length,
    cold: leads.filter(l => l.temperatura === 'COLD').length,
  }), [leads]);

  const columnas: DataTableColumn<Lead>[] = [
    {
      key: 'nombre',
      label: 'Cliente',
      sortable: true,
      render: (row: Lead) => (
        <div>
          <div className="fw-semibold">{row.nombre || 'Sin nombre'}</div>
          <small className="text-muted">{row.telefono}</small>
        </div>
      ),
    },
    {
      key: 'vehiculo_interes',
      label: 'Vehículo',
      render: (row: Lead) => row.vehiculo_interes || <span className="text-muted">—</span>,
    },
    {
      key: 'temperatura',
      label: 'Temperatura',
      sortable: true,
      render: (row: Lead) => (
        <Badge variant={VARIANTE_TEMPERATURA[row.temperatura] || 'secondary'} soft pill>
          {row.temperatura || 'sin clasificar'}
        </Badge>
      ),
    },
    { key: 'lead_score', label: 'Score', sortable: true, render: (row: Lead) => `${row.lead_score}/100` },
    { key: 'presupuesto', label: 'Presupuesto', render: (row: Lead) => pesos(row.presupuesto) },
    { key: 'ultima_interaccion', label: 'Última interacción', render: (row: Lead) => fecha(row.ultima_interaccion) },
    {
      key: 'proxima_accion',
      label: 'Próxima acción',
      render: (row: Lead) => row.proxima_accion || <span className="text-muted">—</span>,
    },
  ];

  const acciones = [
    { label: 'Ver ficha', icon: 'feather-eye', onClick: (row: Lead) => setSeleccionado(row) },
  ];

  return (
    <>
      {modoDemo && (
        <Alert variant="warning" icon="feather-alert-triangle" title="Vista de proyección">
          Estas cifras son de demostración, no mediciones reales de este bot.
          Desactivá “Proyección demo” para ver los datos efectivos.
        </Alert>
      )}
      <PageHeader title="Leads" breadcrumbs={[{ label: 'Asesor Comercial IA', href: '.' }, { label: 'Leads' }]}>
        <div className="form-check form-switch me-3">
          <input
            className="form-check-input"
            type="checkbox"
            role="switch"
            id="toggle-proyeccion"
            checked={modoDemo}
            onChange={(e) => setModoDemo(e.target.checked)}
          />
          <label className="form-check-label" htmlFor="toggle-proyeccion">
            Proyección demo
          </label>
        </div>
      </PageHeader>
      <div className="main-content">
        <div className="row g-4">
          <div className="col-md-4">
            <StatsCard icon="feather-zap" iconBg="bg-soft-danger" value={String(totales.hot)} label="Leads HOT" />
          </div>
          <div className="col-md-4">
            <StatsCard icon="feather-thermometer" iconBg="bg-soft-warning" value={String(totales.warm)} label="Leads WARM" />
          </div>
          <div className="col-md-4">
            <StatsCard icon="feather-cloud" iconBg="bg-soft-info" value={String(totales.cold)} label="Leads COLD" />
          </div>

          <div className="col-12">
            <Card
              title="Leads capturados"
              subtitle="Ordenados por lead score: a quién conviene contactar primero"
              actions={
                <div className="d-flex gap-2">
                  <Input
                    placeholder="Buscar nombre, teléfono o vehículo"
                    value={busqueda}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setBusqueda(e.target.value)}
                  />
                  <Select
                    options={[
                      { value: '', label: 'Todas las temperaturas' },
                      { value: 'HOT', label: 'Solo HOT' },
                      { value: 'WARM', label: 'Solo WARM' },
                      { value: 'COLD', label: 'Solo COLD' },
                    ]}
                    value={temperatura}
                    onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTemperatura(e.target.value)}
                  />
                </div>
              }
            >
              {error && <Alert variant="danger" icon="feather-alert-circle">{error}</Alert>}
              {loading ? (
                <LoadingState />
              ) : leads.length === 0 ? (
                <p className="text-muted mb-0">
                  Todavía no hay leads capturados. Aparecen acá apenas un cliente entrega
                  antecedentes comerciales en una conversación.
                </p>
              ) : (
                <DataTable columns={columnas} data={leads} rowKey="id" pageSize={10} actions={acciones} />
              )}
            </Card>
          </div>
        </div>
      </div>

      <Modal
        open={seleccionado !== null}
        onClose={() => setSeleccionado(null)}
        title={seleccionado?.nombre || 'Ficha del lead'}
        size="lg"
      >
        {seleccionado && (
          <div className="row g-3">
            <div className="col-12">
              <Badge variant={VARIANTE_TEMPERATURA[seleccionado.temperatura] || 'secondary'} soft pill>
                {seleccionado.temperatura} · {seleccionado.lead_score}/100
              </Badge>
            </div>
            {seleccionado.resumen && (
              <div className="col-12">
                <Card title="Resumen de la conversación">
                  <p className="mb-0">{seleccionado.resumen}</p>
                </Card>
              </div>
            )}
            <div className="col-md-6"><strong>Teléfono:</strong> {seleccionado.telefono || '—'}</div>
            <div className="col-md-6"><strong>Email:</strong> {seleccionado.email || '—'}</div>
            <div className="col-md-6"><strong>Comuna:</strong> {seleccionado.comuna || '—'}</div>
            <div className="col-md-6"><strong>Intención:</strong> {seleccionado.intencion || '—'}</div>
            <div className="col-md-6"><strong>Vehículo de interés:</strong> {seleccionado.vehiculo_interes || '—'}</div>
            <div className="col-md-6"><strong>Plazo de compra:</strong> {seleccionado.plazo_compra || '—'}</div>
            <div className="col-md-4"><strong>Presupuesto:</strong> {pesos(seleccionado.presupuesto)}</div>
            <div className="col-md-4"><strong>Pie disponible:</strong> {pesos(seleccionado.pie_disponible)}</div>
            <div className="col-md-4"><strong>Cuota objetivo:</strong> {pesos(seleccionado.cuota_objetivo)}</div>
            <div className="col-md-6">
              <strong>Parte de pago:</strong>{' '}
              {seleccionado.tiene_parte_pago === null
                ? 'no se preguntó'
                : seleccionado.tiene_parte_pago
                  ? `sí — ${seleccionado.vehiculo_actual || 'sin detalle'}`
                  : 'no'}
            </div>
            <div className="col-md-3"><strong>Sentimiento:</strong> {seleccionado.sentimiento || '—'}</div>
            <div className="col-md-3"><strong>Urgencia:</strong> {seleccionado.urgencia || '—'}</div>
            {seleccionado.proxima_accion && (
              <div className="col-12">
                <Alert variant="info" icon="feather-arrow-right" title="Próxima acción">
                  {seleccionado.proxima_accion}
                </Alert>
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
