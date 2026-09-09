import { useEffect, useState } from 'react';
import { Card, Button, Badge, FormField, Input, Select, Alert, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';

interface LlmConfig {
  active_model: string;
  model_override: string;
  model_source: 'override' | 'env';
  key_set: boolean;
  key_override: boolean;
  key_source: 'override' | 'env' | 'none';
  models: string[];
}

export function LlmConfigPanel() {
  const [data, setData] = useState<LlmConfig | null>(null);
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');

  const load = () =>
    apiFetch<LlmConfig>('/cavem/api/admin/llm-config').then(d => { setData(d); setModel(d.model_override); }).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  // Cada save* gatea el toast de exito en si el POST realmente funciono
  // (no en si load() se ejecuto) -- mismo bug de exito-optimista ya
  // encontrado y arreglado en BotStatePanel/PromptPanel/ScrapingConfigPanel;
  // aca se habia colado sin arreglar en la misma pasada del review final.
  const saveModel = async () => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch('/cavem/api/admin/llm-config', { method: 'POST', body: JSON.stringify({ model }) })
      .catch(err => { ok = false; console.error(err); });
    await load();
    if (ok) { setSaved('Modelo guardado'); setTimeout(() => setSaved(''), 2000); }
    else { setError('No se pudo guardar el modelo — intentá de nuevo.'); }
    setSaving(false);
  };

  const saveKey = async () => {
    if (!apiKey) return;
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch('/cavem/api/admin/llm-config', { method: 'POST', body: JSON.stringify({ api_key: apiKey }) })
      .catch(err => { ok = false; console.error(err); });
    setApiKey('');
    await load();
    if (ok) { setSaved('API key guardada'); setTimeout(() => setSaved(''), 2000); }
    else { setError('No se pudo guardar la API key — intentá de nuevo.'); }
    setSaving(false);
  };

  const clearKey = async () => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch('/cavem/api/admin/llm-config', { method: 'POST', body: JSON.stringify({ action: 'clear_key' }) })
      .catch(err => { ok = false; console.error(err); });
    await load();
    if (ok) { setSaved('API key removida (usa env)'); setTimeout(() => setSaved(''), 2000); }
    else { setError('No se pudo limpiar la API key — intentá de nuevo.'); }
    setSaving(false);
  };

  if (loading || !data) return <LoadingState />;

  const modelOptions = [{ value: '', label: `— Usar default (${data.active_model}) —` }, ...data.models.map(m => ({ value: m, label: m }))];

  return (
    <Card title="Modelo & API key (OpenRouter)">
      <FormField label="Modelo activo" hint={`Actualmente: ${data.active_model} · ${data.model_source === 'override' ? 'override del panel' : 'default env'}`}>
        <div className="d-flex gap-2">
          <Select options={modelOptions} value={model} onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setModel(e.target.value)} />
          <Button variant="primary" loading={saving} onClick={saveModel} icon="feather-save">Guardar</Button>
        </div>
      </FormField>

      <FormField label="OpenRouter API key" hint={`Key: ${data.key_set ? 'configurada' : '(ninguna)'} · ${data.key_source === 'override' ? 'override del panel' : data.key_source === 'env' ? 'default env' : 'sin key'}`}>
        <div className="d-flex gap-2 align-items-center">
          <Input type="password" startAddon={<i className="feather-key" />} placeholder="Pegar nueva API key..." value={apiKey}
                 onChange={(e: React.ChangeEvent<HTMLInputElement>) => setApiKey(e.target.value)} />
          <Button variant="primary" loading={saving} disabled={!apiKey} onClick={saveKey} icon="feather-save">Guardar</Button>
          {data.key_override && (
            <Button variant="light-brand" disabled={saving} onClick={clearKey} icon="feather-x">Limpiar</Button>
          )}
        </div>
      </FormField>

      <div className="d-flex gap-2">
        <Badge variant={data.model_source === 'override' ? 'warning' : 'dark'} soft>modelo: {data.model_source}</Badge>
        <Badge variant={data.key_source === 'override' ? 'warning' : data.key_source === 'env' ? 'dark' : 'danger'} soft>key: {data.key_source}</Badge>
      </div>

      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">{saved}</Alert></div>}
      {error && <div className="mt-3"><Alert variant="danger" icon="feather-alert-circle">{error}</Alert></div>}
    </Card>
  );
}
