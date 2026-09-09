import { useEffect, useState } from 'react';
import { Card, PageHeader, Badge, DataTable, Select, StatsCard, LoadingState, Alert } from '@duralux/ui';
import type { BadgeProps, DataTableColumn } from '@duralux/ui';
import { apiFetch } from '../api';

interface Reserva {
  codigo: string;
  cliente: string;
  telefono: string;
  email: string;
  vehiculo: string;
  patente: string;
  vehiculo_anio: number | null;
  vehiculo_km: number | null;
  servicio: string;
  sucursal: string;
  fecha: string;
  hora: string;
  estado: string;
  created_at: string;
}

type VarianteBadge = NonNullable<BadgeProps['variant']>;
const VARIANTE_ESTADO: Record<string, VarianteBadge> = { activa: 'success', cancelada: 'secondary' };

function km(valor: number | null): string {
  return valor === null || valor === undefined ? '—' : `${valor.toLocaleString('es-CL')} km`;
}

export function AgendamientosPage() {
  const [reservas, setReservas] = useState<Reserva[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [estado, setEstado] = useState('activa');
  const [modoDemo, setModoDemo] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams();
    if (estado) params.set('estado', estado);
    if (modoDemo) params.set('modo', 'demo');
    setLoading(true);
    setError('');
    apiFetch<Reserva[]>(`/cavem/api/admin/reservas?${params}`)
      .then(setReservas)
      .catch(err => setError(err.message || 'No se pudieron cargar los agendamientos.'))
      .finally(() => setLoading(false));
  }, [estado, modoDemo]);

  const hoy = new Date().toISOString().slice(0, 10);
  const deHoy = reservas.filter(r => r.fecha === hoy).length;
  const conPatente = reservas.filter(r => r.patente).length;

  const columnas: DataTableColumn<Reserva>[] = [
    {
      key: 'cliente',
      label: 'Cliente',
      sortable: true,
      render: (row: Reserva) => (
        <div>
          <div className="fw-semibold">{row.cliente || 'Sin nombre'}</div>
          <small className="text-muted">{row.telefono}</small>
        </div>
      ),
    },
    {
      key: 'vehiculo',
      label: 'Vehículo',
      render: (row: Reserva) => (
        <div>
          <div>{row.vehiculo || <span className="text-muted">—</span>}</div>
          <small className="text-muted">
            {row.patente || 'sin patente'}
            {row.vehiculo_anio ? ` · ${row.vehiculo_anio}` : ''}
            {row.vehiculo_km ? ` · ${km(row.vehiculo_km)}` : ''}
          </small>
        </div>
      ),
    },
    { key: 'servicio', label: 'Servicio', sortable: true },
    {
      key: 'fecha',
      label: 'Fecha y hora',
      sortable: true,
      render: (row: Reserva) => `${row.fecha} · ${row.hora}`,
    },
    { key: 'sucursal', label: 'Sucursal' },
    {
      key: 'estado',
      label: 'Estado',
      render: (row: Reserva) => (
        <Badge variant={VARIANTE_ESTADO[row.estado] || 'secondary'} soft pill>{row.estado}</Badge>
      ),
    },
    { key: 'codigo', label: 'Código' },
  ];

  return (
    <>
      {modoDemo && (
        <Alert variant="warning" icon="feather-alert-triangle" title="Vista de proyección">
          Estas cifras son de demostración, no mediciones reales de este bot.
          Desactivá “Proyección demo” para ver los datos efectivos.
        </Alert>
      )}
      <PageHeader title="Agendamientos" breadcrumbs={[{ label: 'Auto IA', href: '.' }, { label: 'Agendamientos' }]}>
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
            <StatsCard icon="feather-calendar" iconBg="bg-soft-primary"
              value={String(reservas.length)} label="Horas agendadas" />
          </div>
          <div className="col-md-4">
            <StatsCard icon="feather-clock" iconBg="bg-soft-success"
              value={String(deHoy)} label="Para hoy" />
          </div>
          <div className="col-md-4">
            <StatsCard icon="feather-hash" iconBg="bg-soft-info"
              value={`${conPatente}/${reservas.length || 0}`} label="Con patente registrada" />
          </div>

          <div className="col-12">
            <Card
              title="Horas de taller"
              subtitle="Cada reserva queda con el vehículo y la patente que entra al taller"
              actions={
                <Select
                  options={[
                    { value: 'activa', label: 'Activas' },
                    { value: 'cancelada', label: 'Canceladas' },
                    { value: '', label: 'Todas' },
                  ]}
                  value={estado}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setEstado(e.target.value)}
                />
              }
            >
              {error && <Alert variant="danger" icon="feather-alert-circle">{error}</Alert>}
              {loading ? (
                <LoadingState />
              ) : reservas.length === 0 ? (
                <p className="text-muted mb-0">
                  Todavía no hay horas agendadas. Aparecen acá apenas un cliente reserva
                  una hora de taller por WhatsApp.
                </p>
              ) : (
                <DataTable columns={columnas} data={reservas} rowKey="codigo" pageSize={10} />
              )}
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
