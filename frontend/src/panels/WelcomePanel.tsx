import { Card, Button, Textarea, Alert, LoadingState } from '@duralux/ui';
import { useApiResource } from '../hooks/useApiResource';
import { useState, useEffect } from 'react';

interface WelcomeData { message: string; }

export function WelcomePanel() {
  const { data, loading, saving, saved, save } = useApiResource<WelcomeData>('/cavem/api/admin/welcome');
  const [message, setMessage] = useState('');

  useEffect(() => { if (data) setMessage(data.message); }, [data]);

  if (loading) return <LoadingState />;

  return (
    <Card title="Mensaje de bienvenida">
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
        Se muestra cuando el contacto saluda por primera vez en una conversación IDLE.
      </p>
    </Card>
  );
}
