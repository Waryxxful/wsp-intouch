import { useState } from 'react';
import { Card, Button, Select, FormField, FileInput, Alert, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';

interface EnvioResultado {
  ok: boolean;
  enviados: number;
  optout_saltados: number;
  errores: string[];
}

const CAMPANAS = [
  { value: 'encuesta_servicio_tecnico', label: 'Encuesta Servicio Técnico' },
  { value: 'encuesta_venta_auto_nuevo', label: 'Encuesta Venta Auto Nuevo' },
];

export function EncuestasPanel() {
  const [campaignType, setCampaignType] = useState(CAMPANAS[0].value);
  const [csvText, setCsvText] = useState('');
  const [nombreArchivo, setNombreArchivo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [resultado, setResultado] = useState<EnvioResultado | null>(null);
  const [error, setError] = useState('');

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { setCsvText(String(reader.result || '')); setNombreArchivo(file.name); };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleEnviar = async () => {
    setEnviando(true);
    setError('');
    setResultado(null);
    try {
      const data = await apiFetch<EnvioResultado>('/intouch/api/admin/encuestas/enviar-masivo', {
        method: 'POST',
        body: JSON.stringify({ campaign_type: campaignType, csv_text: csvText }),
      });
      setResultado(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error al enviar la campaña.');
    } finally {
      setEnviando(false);
    }
  };

  return (
    <Card title="Envío masivo de encuestas">
      <FormField label="Campaña">
        <Select
          value={campaignType}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setCampaignType(e.target.value)}
        >
          {CAMPANAS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
        </Select>
      </FormField>
      <FormField label="Lista de contactos (CSV con columnas wa_id, nombre)" className="mt-3">
        <FileInput accept=".csv,text/csv" onChange={handleFile} />
        {nombreArchivo && <p className="fs-11 text-body-secondary mt-1 mb-0">Archivo cargado: {nombreArchivo}</p>}
      </FormField>
      <div className="mt-3">
        <Button variant="primary" loading={enviando} disabled={!csvText} onClick={handleEnviar} icon="feather-send">
          Enviar campaña
        </Button>
      </div>
      {error && <div className="mt-3"><Alert variant="danger" icon="feather-alert-triangle">{error}</Alert></div>}
      {resultado && (
        <div className="mt-3">
          <Alert variant={resultado.errores.length ? 'warning' : 'success'} icon="feather-check-circle">
            Enviados: {resultado.enviados} · Opt-out saltados: {resultado.optout_saltados}
            {resultado.errores.length > 0 && (
              <ul className="mb-0 mt-2">
                {resultado.errores.map((e, i) => <li key={i} className="fs-12">{e}</li>)}
              </ul>
            )}
          </Alert>
        </div>
      )}
    </Card>
  );
}
