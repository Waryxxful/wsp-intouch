import { useEffect, useState } from 'react';
import { Card, Button, Badge, FormField, Select, Alert, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';

interface ScrapingLlmConfig {
  active_model: string;
  model_override: string;
  model_source: 'override' | 'env';
  key_set: boolean;
  models: string[];
}

export function ScrapingLlmConfigPanel() {
  const [data, setData] = useState<ScrapingLlmConfig | null>(null);
  const [model, setModel] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');

  const load = () =>
    apiFetch<ScrapingLlmConfig>('/intouch/api/admin/scraping-llm-config').then(d => { setData(d); setModel(d.model_override); }).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const saveModel = async () => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch('/intouch/api/admin/scraping-llm-config', { method: 'POST', body: JSON.stringify({ model }) })
      .catch(err => { ok = false; console.error(err); });
    await load();
    if (ok) { setSaved('Modelo guardado'); setTimeout(() => setSaved(''), 2000); }
    else { setError('No se pudo guardar el modelo — intentá de nuevo.'); }
    setSaving(false);
  };

  if (loading || !data) return <LoadingState />;

  const modelOptions = [{ value: '', label: `— Usar default (${data.active_model}) —` }, ...data.models.map(m => ({ value: m, label: m }))];

  return (
    // Ya no hay campo de API key acá: desde la migración del 2026-09-02 la
    // extracción usa la key de OpenRouter, que se administra en el panel de
    // LLM del bot (una sola key para bot, media y scraping).
    <Card title="Modelo de extracción — scraping y documentos (OpenRouter)">
      <FormField label="Modelo activo" hint={`Actualmente: ${data.active_model} · ${data.model_source === 'override' ? 'override del panel' : 'default env'}`}>
        <div className="d-flex gap-2">
          <Select options={modelOptions} value={model} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setModel(e.target.value)} />
          <Button variant="primary" loading={saving} onClick={saveModel} icon="feather-save">Guardar</Button>
        </div>
      </FormField>

      <div className="d-flex gap-2">
        <Badge variant={data.model_source === 'override' ? 'warning' : 'dark'} soft>modelo: {data.model_source}</Badge>
        <Badge variant={data.key_set ? 'dark' : 'danger'} soft>key OpenRouter: {data.key_set ? 'configurada' : 'sin key'}</Badge>
      </div>

      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">{saved}</Alert></div>}
      {error && <div className="mt-3"><Alert variant="danger" icon="feather-alert-circle">{error}</Alert></div>}
    </Card>
  );
}
