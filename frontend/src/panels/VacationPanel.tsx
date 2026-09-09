import { Card, Button, Textarea, Alert, LoadingState } from '@duralux/ui';
import { useApiResource } from '../hooks/useApiResource';
import { useState, useEffect } from 'react';

interface VacationData { message: string; }

export function VacationPanel() {
  const { data, loading, saving, saved, save } = useApiResource<VacationData>('/intouch/api/admin/vacation');
  const [message, setMessage] = useState('');

  useEffect(() => { if (data) setMessage(data.message); }, [data]);

  if (loading) return <LoadingState />;

  return (
    <Card title="Mensaje fuera de horario">
      <Textarea
        rows={4}
        value={message}
        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setMessage(e.target.value)}
      />
      <div className="mt-3">
        <Button variant="primary" loading={saving} onClick={() => save({ message })} icon="feather-save">
          Guardar
        </Button>
      </div>
      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">Guardado correctamente.</Alert></div>}
      <p className="fs-11 text-body-secondary mt-3 mb-0">
        Nota: este mensaje se guarda pero todavía no lo consulta el bot en runtime
        (follow-up conocido, igual que el override de prompt) — hoy solo
        `welcome_message` está wireado en `bot/whatsapp/handlers.py`.
      </p>
    </Card>
  );
}
