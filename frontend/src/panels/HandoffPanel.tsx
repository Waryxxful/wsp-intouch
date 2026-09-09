import { useEffect, useState } from 'react';
import { Card, Button, Badge, Alert, LoadingState } from '@duralux/ui';
import { useApiResource } from '../hooks/useApiResource';

interface HandoffData { keywords: string[]; }

export function HandoffPanel() {
  const { data, loading, saving, saved, save } = useApiResource<HandoffData>('/intouch/api/admin/handoff');
  const [keywords, setKeywords] = useState<string[]>([]);
  const [nueva, setNueva] = useState('');

  useEffect(() => { if (data) setKeywords(data.keywords); }, [data]);

  const addKeyword = () => {
    const k = nueva.trim();
    if (!k || keywords.includes(k)) return;
    setKeywords(current => [...current, k]);
    setNueva('');
  };

  const removeKeyword = (k: string) => setKeywords(current => current.filter(x => x !== k));

  if (loading) return <LoadingState />;

  return (
    <Card title="Handoff a humano por palabra clave">
      <div className="d-flex gap-2 mb-3">
        <input
          className="form-control" value={nueva} placeholder="ej. hablar con humano"
          onChange={e => setNueva(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') addKeyword(); }}
        />
        <Button variant="light-brand" onClick={addKeyword} icon="feather-plus">Agregar</Button>
      </div>

      <div className="d-flex flex-wrap gap-2 mb-3">
        {keywords.map(k => (
          <Badge key={k} variant="dark" soft>
            {k}
            <button
              type="button" className="btn-close btn-close-sm ms-2" style={{ fontSize: 8 }}
              onClick={() => removeKeyword(k)} aria-label={`Quitar ${k}`}
            />
          </Badge>
        ))}
      </div>

      <Button variant="primary" loading={saving} onClick={() => save({ keywords })} icon="feather-save">
        Guardar
      </Button>

      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">Guardado correctamente.</Alert></div>}
      <p className="fs-11 text-body-secondary mt-3 mb-0">
        Cuando el mensaje del contacto contiene una de estas palabras, la
        conversación pasa a modo HUMAN automáticamente.
      </p>
    </Card>
  );
}
