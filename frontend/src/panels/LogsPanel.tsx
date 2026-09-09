import { useEffect, useState } from 'react';
import { Card, Button, DataTable, LoadingState, EmptyState } from '@duralux/ui';
import { apiFetch, apiUrl } from '../api';

interface LogEntry {
  id: number; conversation_id: number; wa_id: string; name: string;
  role: string; content: string; created_at: string;
}

export function LogsPanel() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => apiFetch<LogEntry[]>('/cavem/api/admin/logs').then(setLogs).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  if (loading) return <LoadingState />;

  return (
    <Card
      title="Logs de conversación"
      actions={
        <Button variant="light-brand" size="sm" icon="feather-download" onClick={() => { window.location.href = apiUrl('/cavem/api/admin/logs/export'); }}>
          Exportar CSV
        </Button>
      }
    >
      {logs.length === 0 ? (
        <EmptyState title="Sin mensajes" />
      ) : (
        <DataTable
          columns={[
            { key: 'created_at', label: 'Fecha', sortable: true, render: (_row, v) => new Date(v).toLocaleString('es-CL') },
            { key: 'wa_id', label: 'Contacto' },
            { key: 'role', label: 'Rol' },
            { key: 'content', label: 'Mensaje' },
          ]}
          data={logs}
          pageSize={20}
        />
      )}
    </Card>
  );
}
