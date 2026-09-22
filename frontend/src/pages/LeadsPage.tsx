import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, PageHeader, Badge, DataTable, Modal, Select, Icon, LoadingState, Alert } from '@duralux/ui';
import type { BadgeProps, DataTableColumn } from '@duralux/ui';
import { apiFetch } from '../api';

interface Lead {
  id: number;
  conversation_id: number | null;
  telefono: string;
  nombre_completo: string;
  correo: string;
  empresa: string;
  industria: string;
  subtipo_automotriz: string;
  cargo: string;
  pais_ciudad: string;
  situacion_contact_center: string;
  tipo_contact_center: string;
  usa_ia_actualmente: boolean | null;
  canales_actuales: string[];
  volumen_interacciones: string;
  necesidad_principal: string;
  soluciones_interes: string[];
  intencion: string;
  plazo_proyecto: string;
  preferencia_horaria: string;
  lead_score: string;
  solicita_consultoria: boolean;
  solicita_contacto_humano: boolean;
  resumen_conversacion: string;
  siguiente_accion_recomendada: string;
  creado: string;
  actualizado: string;
  notificado: boolean;
  estado_despacho: 'despachado' | 'pendiente' | 'conflicto' | 'sin_destino';
  conflicto_motivo: string;
  crm_contact_id: string;
  crm_deal_id: string;
}

interface RespuestaLeads {
  leads: Lead[];
  sink_activo: boolean;
}

type VarianteBadge = NonNullable<BadgeProps['variant']>;

// "Azul" del brief = primary (#3454d1) y no info (que en esta paleta es
// teal, no azul) -- ver tokens.d.ts de @duralux/ui.
const VARIANTE_SCORE: Record<string, VarianteBadge> = {
  HOT: 'danger',
  WARM: 'warning',
  COLD: 'primary',
  NO_CALIFICADO: 'secondary',
};

const LABEL_SCORE: Record<string, string> = {
  HOT: 'HOT',
  WARM: 'WARM',
  COLD: 'COLD',
  NO_CALIFICADO: 'No calificado',
};

const LABEL_TIPO_CC: Record<string, string> = {
  propio: 'Propio',
  externalizado: 'Externalizado',
  mixto: 'Mixto',
  no_tiene: 'No tiene',
};

// Un guion es mas honesto que una etiqueta inventada para un campo vacio.
function guion(valor: string | null | undefined): string {
  return valor ? valor : '—';
}

// Formato relativo simple ("hace 5 min"), sin libreria: al equipo comercial
// le importa el orden de magnitud (minutos/horas/dias), no el segundo exacto.
function relativo(iso: string): string {
  const entoncesMs = new Date(iso).getTime();
  const diffSeg = Math.round((Date.now() - entoncesMs) / 1000);
  if (diffSeg < 60) return 'hace instantes';
  const diffMin = Math.round(diffSeg / 60);
  if (diffMin < 60) return `hace ${diffMin} min`;
  const diffHoras = Math.round(diffMin / 60);
  if (diffHoras < 24) return `hace ${diffHoras} h`;
  const diffDias = Math.round(diffHoras / 24);
  if (diffDias < 30) return `hace ${diffDias} d`;
  return new Date(iso).toLocaleDateString('es-CL', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function LeadsPage() {
  const navigate = useNavigate();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [leadScore, setLeadScore] = useState('');
  const [seleccionado, setSeleccionado] = useState<Lead | null>(null);
  const [reintentando, setReintentando] = useState<number | null>(null);

  useEffect(() => {
    // Descarte de respuestas viejas: si el filtro cambia rapido, las
    // respuestas pueden llegar desordenadas.
    let vigente = true;
    const params = new URLSearchParams();
    if (leadScore) params.set('lead_score', leadScore);
    setLoading(true);
    setError('');
    apiFetch<RespuestaLeads>(`/intouch/api/leads?${params}`)
      .then(datos => {
        if (!vigente) return;
        setLeads(datos.leads);
      })
      .catch(err => { if (vigente) setError(err.message || 'No se pudieron cargar los leads.'); })
      .finally(() => { if (vigente) setLoading(false); });
    return () => { vigente = false; };
  }, [leadScore]);

  // Reintento manual de despacho al CRM: patchea el lead en memoria con la
  // respuesta del endpoint en vez de recargar la lista entera -- es lo mismo
  // que devuelve GET /api/leads para ese lead, y evita una carrera con el
  // filtro de lead_score si cambió mientras la petición estaba en vuelo.
  const reintentar = async (row: Lead) => {
    setReintentando(row.id);
    setError('');
    try {
      const resp = await apiFetch<Pick<Lead, 'estado_despacho' | 'crm_contact_id' | 'crm_deal_id' | 'conflicto_motivo'>>(
        `/intouch/api/leads/${row.id}/reintentar`,
        { method: 'POST' },
      );
      setLeads(actuales => actuales.map(l => (l.id === row.id ? { ...l, ...resp } : l)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo reintentar el despacho.');
    } finally {
      setReintentando(null);
    }
  };

  const columnas: DataTableColumn<Lead>[] = [
    {
      key: 'empresa',
      label: 'Empresa',
      sortable: true,
      render: (row) => row.empresa || row.nombre_completo || row.telefono,
    },
    {
      key: 'nombre_completo',
      label: 'Contacto',
      render: (row) => (
        <div>
          <div>{guion(row.nombre_completo)}</div>
          {row.cargo && <small className="text-muted">{row.cargo}</small>}
        </div>
      ),
    },
    {
      key: 'telefono',
      label: 'Teléfono',
      render: (row) => (
        <a href={`https://wa.me/${row.telefono}`} target="_blank" rel="noreferrer">
          {row.telefono}
        </a>
      ),
    },
    {
      key: 'correo',
      label: 'Correo',
      render: (row) => guion(row.correo),
    },
    {
      key: 'industria',
      label: 'Industria',
      render: (row) => (
        <div>
          <div>{guion(row.industria)}</div>
          {row.subtipo_automotriz && <small className="text-muted">{row.subtipo_automotriz}</small>}
        </div>
      ),
    },
    {
      key: 'lead_score',
      label: 'Calificación',
      sortable: true,
      render: (row) => (
        <Badge variant={VARIANTE_SCORE[row.lead_score] || 'secondary'} soft pill>
          {LABEL_SCORE[row.lead_score] || 'Sin calificar'}
        </Badge>
      ),
    },
    {
      key: 'necesidad_principal',
      label: 'Necesidad',
      render: (row) => {
        const texto = row.necesidad_principal;
        if (!texto) return '—';
        return texto.length > 80 ? `${texto.slice(0, 80)}…` : texto;
      },
    },
    {
      key: 'situacion_contact_center',
      label: 'Contact Center',
      render: (row) => {
        // situacion_contact_center="no_tiene" fuerza tipo_contact_center a
        // "no_tiene" en el save() del modelo (bot/models.py), asi que basta
        // con leer tipo_contact_center una vez que hay situacion informada.
        if (!row.situacion_contact_center) return '—';
        return LABEL_TIPO_CC[row.tipo_contact_center] || '—';
      },
    },
    {
      key: 'actualizado',
      label: 'Actualizado',
      sortable: true,
      render: (row) => (
        <div className="d-flex align-items-center gap-2">
          <span>{relativo(row.actualizado)}</span>
          {row.notificado && (
            <span title="Ya se notificó como HOT">
              {/* Icon arma la clase como feather-${name}: va el nombre pelado. */}
              <Icon name="bell" size="sm" />
            </span>
          )}
          {row.estado_despacho === 'pendiente' && (
            <span title="Sin despachar al CRM — se reintenta cada 15 minutos" className="text-danger">
              <Icon name="alert-triangle" size="sm" />
            </span>
          )}
          {row.estado_despacho === 'conflicto' && (
            // Distinto del "pendiente" de arriba a propósito: un pendiente se
            // arregla solo con el barrido, un conflicto no -- necesita que
            // una persona decida la identidad correcta en el CRM.
            <span
              title={`Conflicto de identidad, requiere resolución manual en el CRM: ${row.conflicto_motivo || 'sin detalle'}`}
              className="text-warning"
            >
              <Icon name="alert-octagon" size="sm" />
            </span>
          )}
          {row.estado_despacho === 'despachado' && row.crm_contact_id && (
            <span title={`En el CRM: contacto ${row.crm_contact_id}`} className="text-success">
              <Icon name="check-circle" size="sm" />
            </span>
          )}
          {(row.estado_despacho === 'pendiente' || row.estado_despacho === 'conflicto') && (
            // El DataTable real (ver DataTable.jsx en duralux-ui) rinde
            // `actions` como un array fijo de botones por fila, sin soporte
            // para ocultar una acción según el dato de esa fila -- por eso
            // el reintento va acá, condicionado por columna, y no en
            // `acciones` más abajo (que sí es igual para todas las filas).
            //
            // Sigue habilitado sobre un conflicto A PROPÓSITO: es la única
            // vía por la que un conflicto vuelve al circuito, para cuando una
            // persona ya lo resolvió del lado del CRM (ver
            // admin_panel/views.py::api_lead_reintentar).
            <button
              type="button"
              className="btn btn-link btn-sm p-0 border-0"
              title="Reintentar despacho al CRM"
              disabled={reintentando === row.id}
              onClick={() => reintentar(row)}
            >
              <Icon name={reintentando === row.id ? 'loader' : 'refresh-cw'} size="sm" />
            </button>
          )}
        </div>
      ),
    },
  ];

  const acciones = [
    { label: 'Ver detalle', icon: 'feather-eye', onClick: (row: Lead) => setSeleccionado(row) },
  ];

  return (
    <>
      <PageHeader title="Leads" breadcrumbs={[{ label: 'Asesor Comercial IA', href: '.' }, { label: 'Leads' }]} />
      <div className="main-content">
        <div className="row g-4">
          <div className="col-12">
            <Card
              title="Leads capturados"
              subtitle="Ordenados por última actualización: a quién conviene revisar primero"
              actions={
                <Select
                  options={[
                    { value: '', label: 'Todas las calificaciones' },
                    { value: 'HOT', label: 'Solo HOT' },
                    { value: 'WARM', label: 'Solo WARM' },
                    { value: 'COLD', label: 'Solo COLD' },
                    { value: 'NO_CALIFICADO', label: 'Solo no calificados' },
                  ]}
                  value={leadScore}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setLeadScore(e.target.value)}
                />
              }
            >
              {error && <Alert variant="danger" icon="feather-alert-circle">{error}</Alert>}
              {loading ? (
                <LoadingState />
              ) : leads.length === 0 ? (
                <p className="text-muted mb-0">
                  Todavía no hay leads capturados. Aparecen acá apenas un contacto entrega
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
        title={seleccionado ? (seleccionado.empresa || seleccionado.nombre_completo || 'Ficha del lead') : 'Ficha del lead'}
        size="lg"
      >
        {seleccionado && (
          <div className="row g-3">
            <div className="col-12 d-flex align-items-center gap-2">
              <Badge variant={VARIANTE_SCORE[seleccionado.lead_score] || 'secondary'} soft pill>
                {LABEL_SCORE[seleccionado.lead_score] || 'Sin calificar'}
              </Badge>
              {seleccionado.notificado && <Badge variant="info" soft pill>Notificado</Badge>}
              {seleccionado.estado_despacho === 'pendiente' && (
                <Badge variant="danger" soft pill>Sin despachar</Badge>
              )}
              {seleccionado.estado_despacho === 'conflicto' && (
                <Badge variant="warning" soft pill>Conflicto de identidad</Badge>
              )}
              {seleccionado.estado_despacho === 'despachado' && seleccionado.crm_contact_id && (
                <Badge variant="success" soft pill>En el CRM ({seleccionado.crm_contact_id})</Badge>
              )}
            </div>
            {seleccionado.estado_despacho === 'conflicto' && (
              <div className="col-12">
                <Alert variant="warning" icon="feather-alert-octagon" title="Conflicto de identidad en el CRM">
                  {guion(seleccionado.conflicto_motivo)} — requiere que una persona decida la
                  identidad correcta en el CRM antes de reintentar.
                </Alert>
              </div>
            )}
            {seleccionado.necesidad_principal && (
              <div className="col-12">
                <Card title="Necesidad principal">
                  <p className="mb-0">{seleccionado.necesidad_principal}</p>
                </Card>
              </div>
            )}
            {seleccionado.resumen_conversacion && (
              <div className="col-12">
                <Card title="Resumen de la conversación">
                  <p className="mb-0">{seleccionado.resumen_conversacion}</p>
                </Card>
              </div>
            )}
            {seleccionado.siguiente_accion_recomendada && (
              <div className="col-12">
                <Alert variant="info" icon="feather-arrow-right" title="Siguiente acción recomendada">
                  {seleccionado.siguiente_accion_recomendada}
                </Alert>
              </div>
            )}
            <div className="col-md-6"><strong>País / ciudad:</strong> {guion(seleccionado.pais_ciudad)}</div>
            <div className="col-md-6"><strong>Plazo del proyecto:</strong> {guion(seleccionado.plazo_proyecto)}</div>
            <div className="col-md-6"><strong>Preferencia horaria:</strong> {guion(seleccionado.preferencia_horaria)}</div>
            <div className="col-md-6"><strong>Volumen de interacciones:</strong> {guion(seleccionado.volumen_interacciones)}</div>
            <div className="col-md-6">
              <strong>Usa IA actualmente:</strong>{' '}
              {seleccionado.usa_ia_actualmente === null ? '—' : seleccionado.usa_ia_actualmente ? 'Sí' : 'No'}
            </div>
            <div className="col-md-6">
              <strong>Canales actuales:</strong>{' '}
              {seleccionado.canales_actuales.length ? seleccionado.canales_actuales.join(', ') : '—'}
            </div>
            <div className="col-md-6">
              <strong>Soluciones de interés:</strong>{' '}
              {seleccionado.soluciones_interes.length ? seleccionado.soluciones_interes.join(', ') : '—'}
            </div>
            <div className="col-md-6">
              <strong>Solicita consultoría:</strong> {seleccionado.solicita_consultoria ? 'Sí' : 'No'}
            </div>
            <div className="col-md-6">
              <strong>Solicita contacto humano:</strong> {seleccionado.solicita_contacto_humano ? 'Sí' : 'No'}
            </div>
            {seleccionado.conversation_id !== null && (
              <div className="col-12">
                <button
                  type="button"
                  className="btn btn-outline-primary btn-sm"
                  onClick={() => {
                    const conversationId = seleccionado.conversation_id;
                    setSeleccionado(null);
                    navigate(`../chat/${conversationId}`);
                  }}
                >
                  Ver conversación
                </button>
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
