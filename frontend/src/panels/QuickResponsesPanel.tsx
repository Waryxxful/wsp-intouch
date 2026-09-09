import { useState } from 'react';
import { Card, Button, Input, Textarea, FormField, DataTable, LoadingState, EmptyState } from '@duralux/ui';
import { useApiList } from '../hooks/useApiList';

interface QuickResponse { id: number; pattern: string; response: string; priority: number; }

export function QuickResponsesPanel() {
  const { items, loading, saving, create, update, remove } = useApiList<QuickResponse>('/intouch/api/admin/quick-responses');
  const [pattern, setPattern] = useState('');
  const [response, setResponse] = useState('');
  const [priority, setPriority] = useState(0);
  const [editingId, setEditingId] = useState<number | null>(null);

  const resetForm = () => { setPattern(''); setResponse(''); setPriority(0); setEditingId(null); };

  const submit = async () => {
    const ok = editingId != null
      ? await update(editingId, { pattern, response, priority })
      : await create({ pattern, response, priority });
    if (ok) resetForm();
  };

  const startEdit = (qr: QuickResponse) => {
    setEditingId(qr.id); setPattern(qr.pattern); setResponse(qr.response); setPriority(qr.priority);
  };

  if (loading) return <LoadingState />;

  return (
    <Card title="Respuestas rápidas">
      <div className="row g-2 mb-3">
        <div className="col-md-2">
          <FormField label="Patrón">
            <Input value={pattern} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPattern(e.target.value)} />
          </FormField>
        </div>
        <div className="col-md-6">
          <FormField label="Respuesta">
            <Textarea rows={2} value={response} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setResponse(e.target.value)} />
          </FormField>
        </div>
        <div className="col-md-2">
          <label className="fw-semibold d-block mb-1">Prioridad</label>
          <Input type="number" value={priority} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPriority(Number(e.target.value))} />
        </div>
        <div className="col-md-2 d-flex align-items-end">
          <Button variant="primary" loading={saving} disabled={!pattern || !response} onClick={submit} icon="feather-save">
            {editingId != null ? 'Actualizar' : 'Agregar'}
          </Button>
        </div>
      </div>

      {items.length === 0 ? (
        <EmptyState title="Sin respuestas rápidas" message="Agregá la primera con el formulario de arriba." />
      ) : (
        <DataTable
          columns={[
            { key: 'pattern', label: 'Patrón', sortable: true },
            { key: 'response', label: 'Respuesta' },
            { key: 'priority', label: 'Prioridad', sortable: true },
          ]}
          data={items}
          actions={[
            { label: 'Editar', icon: 'feather-edit-2', onClick: startEdit },
            { label: 'Borrar', icon: 'feather-trash-2', onClick: (qr: QuickResponse) => remove(qr.id) },
          ]}
        />
      )}
    </Card>
  );
}
