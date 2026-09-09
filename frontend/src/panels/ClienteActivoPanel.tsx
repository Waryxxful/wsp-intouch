import { useEffect, useState } from 'react';
import { Card, Button, Badge, LoadingState, Modal, Alert } from '@duralux/ui';
import { apiFetch } from '../api';

interface FlipPendiente {
  target_cliente: string;
  solicitado_por: string;
  solicitado_en: string;
}

interface Estado {
  cliente_activo: string;
  opciones: [string, string][];
  flip_pendiente: FlipPendiente | null;
}

export function ClienteActivoPanel() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [loading, setLoading] = useState(true);
  const [flipping, setFlipping] = useState(false);
  const [confirmar, setConfirmar] = useState<string | null>(null);
  const [error, setError] = useState('');

  const load = () =>
    apiFetch<Estado>('/intouch/api/admin/cliente-activo').then(setEstado).catch(e => setError(e.message));

  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const confirmarFlip = async () => {
    if (!confirmar) return;
    setFlipping(true);
    setError('');
    try {
      await apiFetch('/intouch/api/admin/cliente-activo', {
        method: 'POST', body: JSON.stringify({ target: confirmar }),
      });
      setConfirmar(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFlipping(false);
    }
  };

  if (loading) return <LoadingState />;
  if (!estado) return null;

  const labelDe = (clave: string) => estado.opciones.find(([c]) => c === clave)?.[1] ?? clave;

  return (
    <Card title="Marca / cliente activo">
      {error && <Alert variant="danger" dismissible>{error}</Alert>}
      {estado.flip_pendiente && (
        <Alert variant="warning" icon="feather-clock">
          Cambio a {labelDe(estado.flip_pendiente.target_cliente)} en curso — un proceso del servidor hace el
          restart real y puede tardar hasta 1 minuto en aplicarse.
        </Alert>
      )}
      <div className="d-flex align-items-center gap-3 mb-3">
        <span className="fs-13 text-body-secondary">Activo ahora:</span>
        <Badge variant="primary" pill>{labelDe(estado.cliente_activo)}</Badge>
      </div>
      <div className="d-flex flex-wrap gap-2">
        {estado.opciones
          .filter(([clave]) => clave !== estado.cliente_activo)
          .map(([clave, label]) => (
            <Button key={clave} variant="light-brand" icon="feather-repeat" onClick={() => setConfirmar(clave)}>
              Flippear a {label}
            </Button>
          ))}
      </div>
      <p className="fs-12 text-body-secondary mt-3 mb-0">
        Flippear reinicia el bot completo (todas las conversaciones en curso se cortan) para que arranque sirviendo
        la otra marca. Tarda hasta 1 minuto en aplicarse.
      </p>
      <Modal
        open={!!confirmar} onClose={() => setConfirmar(null)}
        title="Confirmar cambio de marca"
        footer={<>
          <Button variant="secondary" onClick={() => setConfirmar(null)}>Cancelar</Button>
          <Button variant="danger" loading={flipping} onClick={confirmarFlip}>Sí, reiniciar y flippear</Button>
        </>}
      >
        {confirmar && (
          <p className="mb-0">
            Esto va a reiniciar el bot para que pase a servir <strong>{labelDe(confirmar)}</strong> en vez de{' '}
            <strong>{labelDe(estado.cliente_activo)}</strong>. Todas las conversaciones activas en este momento se
            van a cortar. ¿Confirmás?
          </p>
        )}
      </Modal>
    </Card>
  );
}
