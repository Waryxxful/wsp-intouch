import { useEffect, useState } from 'react';
import { Card, Button, Badge, FormField, Select, Alert, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';

interface MediaLlmConfig {
  active_model: string;
  model_override: string;
  model_source: 'override' | 'env';
  models: string[];
}

export function MediaLlmConfigPanel() {
  const [data, setData] = useState<MediaLlmConfig | null>(null);
  const [model, setModel] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');

  const load = () =>
    apiFetch<MediaLlmConfig>('/cavem/api/admin/media-llm-config').then(d => { setData(d); setModel(d.model_override); }).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const saveModel = async () => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch('/cavem/api/admin/media-llm-config', { method: 'POST', body: JSON.stringify({ model }) })
      .catch(err => { ok = false; console.error(err); });
    await load();
    if (ok) { setSaved('Modelo guardado'); setTimeout(() => setSaved(''), 2000); }
    else { setError('No se pudo guardar el modelo — intentá de nuevo.'); }
    setSaving(false);
  };

  if (loading || !data) return <LoadingState />;

  const modelOptions = [{ value: '', label: `— Usar default (${data.active_model}) —` }, ...data.models.map(m => ({ value: m, label: m }))];

  return (
    <Card title="Modelo para imagen y audio (OpenRouter)">
      <FormField label="Modelo activo" hint={`Actualmente: ${data.active_model} · ${data.model_source === 'override' ? 'override del panel' : 'default env'}`}>
        <div className="d-flex gap-2">
          <Select options={modelOptions} value={model} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setModel(e.target.value)} />
          <Button variant="primary" loading={saving} onClick={saveModel} icon="feather-save">Guardar</Button>
        </div>
      </FormField>

      <Badge variant={data.model_source === 'override' ? 'warning' : 'dark'} soft>modelo: {data.model_source}</Badge>

      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">{saved}</Alert></div>}
      {error && <div className="mt-3"><Alert variant="danger" icon="feather-alert-circle">{error}</Alert></div>}
    </Card>
  );
}
