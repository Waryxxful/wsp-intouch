import { useEffect, useState } from 'react';
import { Card, Button, Badge, Input, Textarea, FormField, Modal, DataTable, LoadingState, EmptyState, Alert } from '@duralux/ui';
import { apiFetch } from '../api';
import { useApiList } from '../hooks/useApiList';

interface EscenarioDePrueba {
  id: number;
  nombre: string;
  persona: string;
  objetivo: string;
  criterios: string[];
  fuente: string;
  max_turns: number;
  activo: boolean;
}

const ESCENARIO_VACIO = { nombre: '', persona: '', objetivo: '', criterios: '', fuente: '', max_turns: 12 };

function EscenariosView({ onTriggered }: { onTriggered: () => void }) {
  const { items, loading, saving, error, create, update, remove } = useApiList<EscenarioDePrueba>('/cavem/api/admin/test-scenarios');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<EscenarioDePrueba | null>(null);
  const [form, setForm] = useState(ESCENARIO_VACIO);
  const [runError, setRunError] = useState('');
  const [triggering, setTriggering] = useState<string | null>(null);

  const openCreate = () => { setEditing(null); setForm(ESCENARIO_VACIO); setModalOpen(true); };

  const openEdit = (e: EscenarioDePrueba) => {
    setEditing(e);
    setForm({
      nombre: e.nombre, persona: e.persona, objetivo: e.objetivo,
      criterios: e.criterios.join('\n'), fuente: e.fuente, max_turns: e.max_turns,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const criterios = form.criterios.split('\n').map(c => c.trim()).filter(Boolean);
    const body: Record<string, unknown> = {
      persona: form.persona, objetivo: form.objetivo, criterios,
      fuente: form.fuente, max_turns: form.max_turns,
    };
    if (!editing) body.nombre = form.nombre;
    const ok = editing ? await update(editing.id, body) : await create(body);
    if (ok) setModalOpen(false);
  };

  const toggleActivo = (e: EscenarioDePrueba) =>
    update(e.id, {
      persona: e.persona, objetivo: e.objetivo, criterios: e.criterios,
      fuente: e.fuente, max_turns: e.max_turns, activo: !e.activo,
    });

  const dispararCorrida = async (nombreEscenario?: string) => {
    const mensajeConfirm = nombreEscenario
      ? `¿Correr el escenario "${nombreEscenario}"? Esto ejecuta una conversacion real contra el LLM y tiene un costo real.`
      : '¿Correr todos los escenarios activos? Esto ejecuta conversaciones reales contra el LLM y tiene un costo real.';
    if (!window.confirm(mensajeConfirm)) return;
    setRunError('');
    setTriggering(nombreEscenario || 'todos');
    try {
      await apiFetch('/cavem/api/admin/test-runs/trigger', {
        method: 'POST', body: JSON.stringify({ nombre_escenario: nombreEscenario || null }),
      });
      onTriggered();
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Ocurrió un error inesperado.');
    }
    setTriggering(null);
  };

  if (loading) return <LoadingState />;

  return (
    <>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <Button variant="primary" icon="feather-plus" onClick={openCreate}>Nuevo escenario</Button>
        <Button
          variant="success" icon="feather-play" loading={triggering === 'todos'}
          disabled={triggering != null} onClick={() => dispararCorrida()}
        >
          Correr todos los activos
        </Button>
      </div>
      {runError && <Alert variant="danger" className="mb-3" icon="feather-alert-triangle">{runError}</Alert>}
      {error && <Alert variant="danger" className="mb-3" icon="feather-alert-triangle">{error}</Alert>}

      {items.length === 0 ? (
        <EmptyState title="Sin escenarios" message="Agregá el primero con el botón de arriba." />
      ) : (
        <DataTable
          columns={[
            { key: 'nombre', label: 'Nombre', sortable: true },
            { key: 'persona', label: 'Persona' },
            { key: 'max_turns', label: 'Turnos máx.' },
            { key: 'activo', label: 'Activo', render: (_row, v) => <Badge variant={v ? 'success' : 'secondary'} soft>{v ? 'sí' : 'no'}</Badge> },
          ]}
          data={items}
          actions={[
            { label: 'Correr', icon: 'feather-play', onClick: (e: EscenarioDePrueba) => dispararCorrida(e.nombre) },
            { label: 'Editar', icon: 'feather-edit-2', onClick: openEdit },
            { label: 'Activar/Desactivar', icon: 'feather-toggle-left', onClick: toggleActivo },
            {
              label: 'Borrar', icon: 'feather-trash-2',
              onClick: (e: EscenarioDePrueba) => {
                if (!window.confirm(`¿Borrar el escenario "${e.nombre}"? Esta accion no se puede deshacer.`)) return;
                remove(e.id);
              },
            },
          ]}
        />
      )}

      <Modal
        open={modalOpen} onClose={() => setModalOpen(false)}
        title={editing ? `Editar ${editing.nombre}` : 'Nuevo escenario'} size="lg" scrollable
      >
        {!editing && (
          <FormField label="Nombre (identificador corto, no editable después)">
            <Input value={form.nombre} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, nombre: e.target.value })} />
          </FormField>
        )}
        <FormField label="Persona (personalidad del cliente simulado)">
          <Textarea rows={3} value={form.persona} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setForm({ ...form, persona: e.target.value })} />
        </FormField>
        <FormField label="Objetivo (qué intenta lograr el cliente simulado)">
          <Textarea rows={3} value={form.objetivo} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setForm({ ...form, objetivo: e.target.value })} />
        </FormField>
        <FormField label="Criterios (uno por línea — lo que evalúa el juez al cierre)">
          <Textarea rows={4} value={form.criterios} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setForm({ ...form, criterios: e.target.value })} />
        </FormField>
        <FormField label="Fuente (referencia humana, opcional)">
          <Input value={form.fuente} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, fuente: e.target.value })} />
        </FormField>
        <FormField label="Turnos máximos">
          <Input
            type="number" value={form.max_turns}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, max_turns: Number(e.target.value) })}
          />
        </FormField>
        <div className="d-flex gap-2 mt-3">
          <Button
            variant="primary" loading={saving} icon="feather-save"
            disabled={!form.persona || !form.objetivo || !form.criterios || (!editing && !form.nombre)}
            onClick={submit}
          >
            Guardar
          </Button>
        </div>
      </Modal>
    </>
  );
}

interface CorridaResumen {
  id: number;
  fecha_inicio: string;
  fecha_fin: string | null;
  estado: string;
  disparada_por: string;
  nombre_escenario_filtro: string | null;
  total: number;
  pasaron: number;
}

interface ResultadoDeEscenario {
  id: number;
  escenario: string;
  paso: boolean | null;
  fallos: string[];
  transcript: string;
  error: string | null;
}

interface CorridaDetalle extends CorridaResumen { resultados: ResultadoDeEscenario[]; }

function EstadoBadge({ estado }: { estado: string }) {
  const variant = estado === 'completa' ? 'success' : estado === 'corriendo' ? 'info' : estado === 'interrumpida' ? 'warning' : 'danger';
  return <Badge variant={variant} soft>{estado}</Badge>;
}

function CorridaDetalleModal({ id, onClose }: { id: number; onClose: () => void }) {
  const [detalle, setDetalle] = useState<CorridaDetalle | null>(null);

  useEffect(() => {
    let activo = true;
    let intervalId: ReturnType<typeof setInterval> | undefined;
    const tick = async () => {
      const data = await apiFetch<CorridaDetalle>(`/cavem/api/admin/test-runs/${id}`).catch(() => null);
      if (!activo || !data) return;
      setDetalle(data);
      if (data.estado !== 'corriendo' && intervalId) clearInterval(intervalId);
    };
    tick();
    intervalId = setInterval(tick, 3000);
    return () => { activo = false; if (intervalId) clearInterval(intervalId); };
  }, [id]);

  return (
    <Modal open onClose={onClose} title={`Corrida #${id}`} size="lg" scrollable>
      {!detalle && <LoadingState />}
      {detalle && (
        <>
          <p className="fs-13 d-flex align-items-center gap-2">
            <EstadoBadge estado={detalle.estado} />
            <span>{detalle.pasaron}/{detalle.total} escenarios pasaron</span>
          </p>
          {detalle.resultados.map(r => (
            <div key={r.id} className="border rounded p-2 mb-2">
              <div className="d-flex justify-content-between align-items-center">
                <strong className="fs-13">{r.escenario}</strong>
                <Badge variant={r.paso ? 'success' : 'danger'} soft>{r.paso ? 'pasó' : 'falló'}</Badge>
              </div>
              {r.error && <Alert variant="danger" className="mt-2 mb-0">{r.error}</Alert>}
              {r.fallos.length > 0 && (
                <ul className="fs-12 mb-0 mt-2">
                  {r.fallos.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
              )}
              {r.transcript && (
                <details className="mt-2">
                  <summary className="fs-12">Ver transcript</summary>
                  <pre className="small bg-light p-2 rounded">{r.transcript}</pre>
                </details>
              )}
            </div>
          ))}
        </>
      )}
    </Modal>
  );
}

function HistorialView() {
  const [corridas, setCorridas] = useState<CorridaResumen[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    let activo = true;
    let intervalId: ReturnType<typeof setInterval> | undefined;
    const tick = () => apiFetch<CorridaResumen[]>('/cavem/api/admin/test-runs')
      .then(data => {
        if (!activo) return;
        setCorridas(data);
        // Nada "corriendo" -> no hay nada que pueda cambiar de estado, asi
        // que se corta el polling. Si el operador vuelve a esta pestaña mas
        // tarde, el remount de HistorialView lo reinicia.
        if (!data.some(c => c.estado === 'corriendo') && intervalId) clearInterval(intervalId);
      })
      .catch(console.error)
      .finally(() => { if (activo) setLoading(false); });
    tick();
    intervalId = setInterval(tick, 4000);
    return () => { activo = false; if (intervalId) clearInterval(intervalId); };
  }, []);

  if (loading) return <LoadingState />;

  return (
    <>
      {corridas.length === 0 ? (
        <EmptyState title="Sin corridas todavía" message="Dispará una corrida desde la pestaña Escenarios." />
      ) : (
        <DataTable
          columns={[
            { key: 'fecha_inicio', label: 'Fecha', sortable: true, render: (_row, v) => new Date(v).toLocaleString('es-CL') },
            { key: 'disparada_por', label: 'Disparada por' },
            { key: 'nombre_escenario_filtro', label: 'Filtro', render: (_row, v) => v || 'todos' },
            { key: 'estado', label: 'Estado', render: (_row, v) => <EstadoBadge estado={v} /> },
            { key: 'pasaron', label: 'Resultado', render: (row, v) => `${v}/${row.total}` },
          ]}
          data={corridas}
          actions={[{ label: 'Ver detalle', icon: 'feather-eye', onClick: (c: CorridaResumen) => setSelectedId(c.id) }]}
        />
      )}
      {selectedId != null && <CorridaDetalleModal id={selectedId} onClose={() => setSelectedId(null)} />}
    </>
  );
}

const VISTAS = [
  { key: 'escenarios', label: 'Escenarios' },
  { key: 'historial', label: 'Historial de corridas' },
] as const;

export function TestScenariosPanel() {
  const [vista, setVista] = useState<(typeof VISTAS)[number]['key']>('escenarios');
  return (
    <Card title="Tester">
      <div className="d-flex gap-2 mb-3">
        {VISTAS.map(v => (
          <Button
            key={v.key} size="sm" variant={vista === v.key ? 'primary' : 'light-brand'}
            onClick={() => setVista(v.key)}
          >
            {v.label}
          </Button>
        ))}
      </div>
      {vista === 'escenarios' ? <EscenariosView onTriggered={() => setVista('historial')} /> : <HistorialView />}
    </Card>
  );
}
