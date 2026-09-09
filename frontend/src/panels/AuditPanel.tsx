import { useEffect, useState } from 'react';
import { Card, DataTable, LoadingState, EmptyState } from '@duralux/ui';
import { apiFetch } from '../api';

interface AuditEntry {
  id: number; user: string; action: string; target: string; details: string; created_at: string;
}

export function AuditPanel() {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch<AuditEntry[]>('/cavem/api/admin/audit').then(setLogs).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingState />;

  return (
    <Card title="Auditoría de acciones del panel">
      {logs.length === 0 ? (
        <EmptyState title="Sin registros" />
      ) : (
        <DataTable
          columns={[
            { key: 'created_at', label: 'Fecha', sortable: true, render: (_row, v) => new Date(v).toLocaleString('es-CL') },
            { key: 'user', label: 'Usuario' },
            { key: 'action', label: 'Acción' },
            { key: 'target', label: 'Sobre' },
            { key: 'details', label: 'Detalle' },
          ]}
          data={logs}
          pageSize={20}
        />
      )}
    </Card>
  );
}
