import { useState } from 'react';
import { Card, Button, Input, FormField, Select, Badge, DataTable, LoadingState, EmptyState } from '@duralux/ui';
import { useApiList } from '../hooks/useApiList';

interface Filtro { id: number; wa_id: string; tipo: 'block' | 'allow'; }

export function FiltersPanel() {
  const { items, loading, saving, create, remove } = useApiList<Filtro>('/intouch/api/admin/filters');
  const [waId, setWaId] = useState('');
  const [tipo, setTipo] = useState<'block' | 'allow'>('block');

  const submit = async () => {
    const ok = await create({ wa_id: waId, tipo });
    if (ok) setWaId('');
  };

  if (loading) return <LoadingState />;

  return (
    <Card title="Filtros de contacto">
      <div className="row g-2 mb-3">
        <div className="col-md-5">
          <FormField label="Número de WhatsApp">
            <Input value={waId} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWaId(e.target.value)} placeholder="56911112222" />
          </FormField>
        </div>
        <div className="col-md-3">
          <FormField label="Tipo">
            <Select
              options={[{ value: 'block', label: 'Bloqueo' }, { value: 'allow', label: 'Permitido' }]}
              value={tipo}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setTipo(e.target.value as 'block' | 'allow')}
            />
          </FormField>
        </div>
        <div className="col-md-2 d-flex align-items-end">
          <Button variant="primary" loading={saving} disabled={!waId} onClick={submit} icon="feather-plus">
            Agregar
          </Button>
        </div>
      </div>

      {items.length === 0 ? (
        <EmptyState title="Sin filtros" message="Agregá el primero con el formulario de arriba." />
      ) : (
        <DataTable
          columns={[
            { key: 'wa_id', label: 'Número' },
            { key: 'tipo', label: 'Tipo', render: (_row, v) => <Badge variant={v === 'block' ? 'danger' : 'success'} soft>{v === 'block' ? 'Bloqueo' : 'Permitido'}</Badge> },
          ]}
          data={items}
          actions={[{ label: 'Borrar', icon: 'feather-trash-2', onClick: (f: Filtro) => remove(f.id) }]}
        />
      )}
    </Card>
  );
}
