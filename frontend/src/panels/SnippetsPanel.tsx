import { useState } from 'react';
import { Card, Button, Input, Textarea, FormField, DataTable, LoadingState, EmptyState } from '@duralux/ui';
import { useApiList } from '../hooks/useApiList';

interface Snippet { id: number; nombre: string; texto: string; }

export function SnippetsPanel() {
  const { items, loading, saving, create, update, remove } = useApiList<Snippet>('/intouch/api/admin/snippets');
  const [nombre, setNombre] = useState('');
  const [texto, setTexto] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);

  const resetForm = () => { setNombre(''); setTexto(''); setEditingId(null); };

  const submit = async () => {
    const ok = editingId != null
      ? await update(editingId, { nombre, texto })
      : await create({ nombre, texto });
    if (ok) resetForm();
  };

  const startEdit = (s: Snippet) => { setEditingId(s.id); setNombre(s.nombre); setTexto(s.texto); };

  if (loading) return <LoadingState />;

  return (
    <Card title="Plantillas de texto">
      <div className="row g-2 mb-3">
        <div className="col-md-3">
          <FormField label="Nombre">
            <Input value={nombre} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNombre(e.target.value)} />
          </FormField>
        </div>
        <div className="col-md-7">
          <FormField label="Texto">
            <Textarea rows={2} value={texto} onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setTexto(e.target.value)} />
          </FormField>
        </div>
        <div className="col-md-2 d-flex align-items-end">
          <Button variant="primary" loading={saving} disabled={!nombre || !texto} onClick={submit} icon="feather-save">
            {editingId != null ? 'Actualizar' : 'Agregar'}
          </Button>
        </div>
      </div>

      {items.length === 0 ? (
        <EmptyState title="Sin plantillas" message="Agregá la primera con el formulario de arriba." />
      ) : (
        <DataTable
          columns={[
            { key: 'nombre', label: 'Nombre', sortable: true },
            { key: 'texto', label: 'Texto' },
          ]}
          data={items}
          actions={[
            { label: 'Editar', icon: 'feather-edit-2', onClick: startEdit },
            { label: 'Borrar', icon: 'feather-trash-2', onClick: (s: Snippet) => remove(s.id) },
          ]}
        />
      )}
    </Card>
  );
}
