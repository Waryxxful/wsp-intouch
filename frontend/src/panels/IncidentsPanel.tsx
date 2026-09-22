import { useEffect, useState } from 'react';
import { Modal, Badge, Button, LoadingState, EmptyState } from '@duralux/ui';
import { apiFetch } from '../api';
import { whenCurrent } from '../chat/chatState';

interface Incident {
  id: number;
  kind: string;
  status: 'abierto' | 'revisado' | 'cerrado';
  context: Record<string, unknown>;
  created_at: string;
}

const STATUS_VARIANT: Record<Incident['status'], 'warning' | 'info' | 'success'> = {
  abierto: 'warning',
  revisado: 'info',
  cerrado: 'success',
};

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('es-CL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function IncidentsPanel({ conversationId, open, onClose, onChanged }: {
  conversationId: number;
  open: boolean;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [items, setItems] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(false);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  // Atado a la conversación del render: si el operador cambia de chat con la
  // carga en vuelo, el efecto la aborta y los incidentes de la conversación
  // anterior no aparecen en el panel de la nueva.
  const load = (signal: AbortSignal) => {
    setLoading(true);
    return whenCurrent(
      signal,
      apiFetch<{ items: Incident[] }>(`/intouch/api/conversations/${conversationId}/incidents`, { signal }),
      data => setItems(data.items),
    ).then(() => { if (!signal.aborted) setLoading(false); });
  };

  useEffect(() => {
    if (!open) return;
    const scope = new AbortController();
    setItems([]);
    load(scope.signal);
    return () => scope.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, conversationId]);

  const updateStatus = (id: number, status: Incident['status']) => {
    setUpdatingId(id);
    apiFetch(`/intouch/api/incidents/${id}/status`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    })
      .then(() => {
        setItems(current => current.map(i => (i.id === id ? { ...i, status } : i)));
        onChanged?.();
      })
      .finally(() => setUpdatingId(null));
  };

  return (
    <Modal open={open} onClose={onClose} title="Incidentes de esta conversación" size="lg">
      {loading ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState icon="feather-check-circle" title="Sin incidentes" message="Esta conversación no tiene incidentes registrados." />
      ) : (
        <div className="d-flex flex-column gap-3">
          {items.map(i => (
            <div key={i.id} className="border rounded-3 p-3">
              <div className="d-flex align-items-start justify-content-between gap-2 mb-2">
                <div className="d-flex align-items-center gap-2">
                  <Badge variant="dark" soft>{i.kind}</Badge>
                  <Badge variant={STATUS_VARIANT[i.status]} soft>{i.status}</Badge>
                </div>
                <span className="fs-11 text-body-secondary">{formatDateTime(i.created_at)}</span>
              </div>
              {Boolean(i.context?.reason || i.context?.motivo || i.context?.resumen) && (
                <p className="fs-13 mb-2">
                  {String(i.context.reason ?? i.context.motivo ?? i.context.resumen)}
                </p>
              )}
              {i.status !== 'cerrado' && (
                <div className="d-flex gap-2">
                  {i.status === 'abierto' && (
                    <Button
                      size="sm" variant="light-brand" loading={updatingId === i.id}
                      onClick={() => updateStatus(i.id, 'revisado')}
                    >
                      Marcar revisado
                    </Button>
                  )}
                  <Button
                    size="sm" variant="success" loading={updatingId === i.id}
                    onClick={() => updateStatus(i.id, 'cerrado')}
                  >
                    Cerrar
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
