import { useEffect, useState } from 'react';
import { Card, Button, Badge, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';

export function BotStatePanel() {
  const [active, setActive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = () =>
    apiFetch<{ active: boolean }>('/cavem/api/admin/bot-state').then(d => setActive(d.active)).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const toggle = async () => {
    setSaving(true);
    await apiFetch('/cavem/api/admin/bot-state', {
      method: 'POST', body: JSON.stringify({ active: !active }),
    }).catch(console.error);
    // Re-fetch en vez de invertir el estado a ciegas: si el POST fallo (sesion
    // expirada, 500), el operador tiene que ver el estado REAL del backend,
    // no un flip optimista que le haga creer que el bot se apago cuando en
    // realidad sigue respondiendo mensajes de WhatsApp.
    await load();
    setSaving(false);
  };

  if (loading) return <LoadingState />;

  return (
    <Card title="Estado del bot">
      <div className="d-flex align-items-center gap-3">
        <Badge variant={active ? 'success' : 'secondary'}>{active ? 'Activo' : 'Inactivo'}</Badge>
        <Button
          variant={active ? 'danger' : 'success'} loading={saving}
          onClick={toggle} icon={active ? 'feather-power' : 'feather-zap'}
        >
          {active ? 'Desactivar bot' : 'Activar bot'}
        </Button>
      </div>
      <p className="fs-12 text-body-secondary mt-3 mb-0">
        Cuando el bot está desactivado, no responde a ningún mensaje entrante.
      </p>
    </Card>
  );
}
