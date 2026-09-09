import { useEffect, useState } from 'react';
import { Card, Button, Badge, Textarea, Alert, Select, Input, FormField, FileInput, LoadingState } from '@duralux/ui';
import { apiFetch } from '../api';
import { useApiList } from '../hooks/useApiList';

interface PromptData {
  agente: string;
  prompt: string;
  is_default: boolean;
  agentes: string[];
}

interface CustomSpecialist {
  id: number;
  slug: string;
  label: string;
  descripcion: string;
  prompt: string;
}

interface PromptVersionInfo {
  id: number;
  created_at: string;
  activa: boolean;
}

const AGENT_LABELS: Record<string, string> = {
  global: 'Comportamiento general (aplica a todos)',
  agendamiento: 'Agendamiento',
  confirmacion: 'Confirmación de recordatorio',
  faq: 'Preguntas libres (FAQ)',
  encuesta_servicio_tecnico: 'Encuesta Servicio Técnico',
  encuesta_venta_auto_nuevo: 'Encuesta Venta Auto Nuevo',
};

const NEW_SPECIALIST = '__new__';

function FileUploadButton({ onText }: { onText: (text: string) => void }) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => onText(String(reader.result || ''));
    reader.readAsText(file);
    e.target.value = '';
  };
  return (
    <FileInput
      className="mt-2"
      accept=".txt,.md,text/plain"
      onChange={handleChange}
      helpText="Subir un archivo reemplaza el texto del prompt."
    />
  );
}

export function PromptPanel() {
  const {
    items: customSpecialists, loading: loadingCustom, saving: savingCustom, error: customError,
    create: createCustom, update: updateCustom, remove: removeCustom, reload: reloadCustom,
  } = useApiList<CustomSpecialist>('/intouch/api/admin/specialists');

  const [selected, setSelected] = useState('agendamiento');
  const isStatic = selected in AGENT_LABELS;
  const isNew = selected === NEW_SPECIALIST;
  const selectedCustom = customSpecialists.find(s => s.slug === selected);

  const agenteKey = isStatic ? selected : selectedCustom ? `custom:${selected}` : '';
  const [versions, setVersions] = useState<PromptVersionInfo[]>([]);
  const [restoringId, setRestoringId] = useState<number | null>(null);

  const loadVersions = (agente: string) =>
    apiFetch<PromptVersionInfo[]>(`/intouch/api/admin/prompt-versions?agente=${agente}`)
      .then(setVersions)
      .catch(console.error);

  useEffect(() => {
    if (agenteKey) loadVersions(agenteKey); else setVersions([]);
  }, [agenteKey]);

  const restoreVersion = async (versionId: number) => {
    setRestoringId(versionId);
    await apiFetch('/intouch/api/admin/prompt-versions/restore', {
      method: 'POST', body: JSON.stringify({ agente: agenteKey, version_id: versionId }),
    }).catch(console.error);
    if (isStatic) await loadStatic(selected);
    else await reloadCustom();
    await loadVersions(agenteKey);
    setRestoringId(null);
  };

  const [staticPrompt, setStaticPrompt] = useState('');
  const [isDefault, setIsDefault] = useState(false);
  const [loadingStatic, setLoadingStatic] = useState(true);
  const [savingStatic, setSavingStatic] = useState(false);
  const [saved, setSaved] = useState(false);

  const loadStatic = (agente: string) =>
    apiFetch<PromptData>(`/intouch/api/admin/prompt?agente=${agente}`)
      .then(d => { setStaticPrompt(d.prompt); setIsDefault(d.is_default); })
      .catch(console.error);

  useEffect(() => {
    if (isStatic) loadStatic(selected).finally(() => setLoadingStatic(false));
  }, [selected]);

  const saveStatic = async () => {
    setSavingStatic(true);
    let ok = true;
    await apiFetch(`/intouch/api/admin/prompt?agente=${selected}`, {
      method: 'POST', body: JSON.stringify({ prompt: staticPrompt }),
    }).catch(err => { ok = false; console.error(err); });
    await loadStatic(selected);
    await loadVersions(agenteKey);
    if (ok) { setSaved(true); setTimeout(() => setSaved(false), 2000); }
    setSavingStatic(false);
  };

  const restoreStatic = async () => {
    if (!confirm('¿Restaurar prompt al valor por defecto? Se perderá el override actual.')) return;
    setSavingStatic(true);
    await apiFetch(`/intouch/api/admin/prompt?agente=${selected}`, {
      method: 'POST', body: JSON.stringify({ action: 'reset' }),
    }).catch(console.error);
    await loadStatic(selected);
    await loadVersions(agenteKey);
    setSavingStatic(false);
  };

  const [customPrompt, setCustomPrompt] = useState('');
  const [customDescripcion, setCustomDescripcion] = useState('');

  useEffect(() => {
    if (selectedCustom) {
      setCustomPrompt(selectedCustom.prompt);
      setCustomDescripcion(selectedCustom.descripcion);
    }
  }, [selectedCustom?.id, selectedCustom?.prompt, selectedCustom?.descripcion]);

  const saveCustom = async () => {
    if (!selectedCustom) return;
    const ok = await updateCustom(selectedCustom.id, { descripcion: customDescripcion, prompt: customPrompt });
    if (ok) { setSaved(true); setTimeout(() => setSaved(false), 2000); }
    await loadVersions(agenteKey);
  };

  const deleteCustom = async () => {
    if (!selectedCustom) return;
    if (!confirm(`¿Borrar el especialista "${selectedCustom.label}"? Esta acción no se puede deshacer.`)) return;
    await removeCustom(selectedCustom.id);
    setSelected('agendamiento');
  };

  const [newLabel, setNewLabel] = useState('');
  const [newDescripcion, setNewDescripcion] = useState('');
  const [newPrompt, setNewPrompt] = useState('');

  const createSpecialist = async () => {
    const ok = await createCustom({ label: newLabel, descripcion: newDescripcion, prompt: newPrompt });
    if (ok) {
      setNewLabel(''); setNewDescripcion(''); setNewPrompt('');
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    }
  };

  if ((isStatic && loadingStatic) || loadingCustom) return <LoadingState />;

  const options = [
    ...Object.keys(AGENT_LABELS).map(a => ({ value: a, label: AGENT_LABELS[a] })),
    ...customSpecialists.map(s => ({ value: s.slug, label: s.label })),
    { value: NEW_SPECIALIST, label: '+ Agregar especialista nuevo' },
  ];

  return (
    <Card
      title="Prompt del sistema"
      actions={isStatic && isDefault && <Badge variant="info" soft>Prompt por defecto</Badge>}
    >
      <div className="mb-3" style={{ maxWidth: 320 }}>
        <Select
          options={options}
          value={selected}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSelected(e.target.value)}
        />
      </div>

      {isStatic && (
        <>
          <Textarea
            rows={16}
            className="font-monospace fs-12"
            value={staticPrompt}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setStaticPrompt(e.target.value)}
          />
          <FileUploadButton onText={setStaticPrompt} />
          <div className="d-flex gap-2 mt-3">
            <Button variant="primary" loading={savingStatic} onClick={saveStatic} icon="feather-save">Guardar prompt</Button>
            <Button variant="light-brand" disabled={savingStatic} onClick={restoreStatic} icon="feather-rotate-ccw">Restaurar por defecto</Button>
          </div>
        </>
      )}

      {selectedCustom && (
        <>
          <FormField label="Descripción corta (para el supervisor)">
            <Input
              value={customDescripcion}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCustomDescripcion(e.target.value)}
            />
          </FormField>
          <Textarea
            rows={16}
            className="font-monospace fs-12 mt-2"
            value={customPrompt}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setCustomPrompt(e.target.value)}
          />
          <FileUploadButton onText={setCustomPrompt} />
          <div className="d-flex gap-2 mt-3">
            <Button variant="primary" loading={savingCustom} onClick={saveCustom} icon="feather-save">Guardar prompt</Button>
            <Button variant="danger" disabled={savingCustom} onClick={deleteCustom} icon="feather-trash-2">Borrar especialista</Button>
          </div>
        </>
      )}

      {isNew && (
        <>
          <FormField label="Nombre">
            <Input value={newLabel} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewLabel(e.target.value)} />
          </FormField>
          <FormField label="Descripción corta (para el supervisor)">
            <Input value={newDescripcion} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewDescripcion(e.target.value)} />
          </FormField>
          <Textarea
            rows={16}
            className="font-monospace fs-12 mt-2"
            value={newPrompt}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setNewPrompt(e.target.value)}
          />
          <FileUploadButton onText={setNewPrompt} />
          <div className="d-flex gap-2 mt-3">
            <Button
              variant="primary" loading={savingCustom}
              disabled={!newLabel || !newDescripcion || !newPrompt}
              onClick={createSpecialist} icon="feather-plus"
            >
              Crear especialista
            </Button>
          </div>
        </>
      )}

      {agenteKey && !isNew && (
        <div className="mt-4">
          <h6 className="fs-13 fw-semibold mb-2">Historial de versiones</h6>
          {versions.length === 0 && (
            <p className="fs-12 text-body-secondary">Todavía no hay versiones guardadas.</p>
          )}
          {versions.map(v => (
            <div key={v.id} className="d-flex align-items-center justify-content-between border-bottom py-2">
              <span className="fs-12">
                {new Date(v.created_at).toLocaleString()}
                {v.activa && <Badge variant="success" soft className="ms-2">Activa</Badge>}
              </span>
              {!v.activa && (
                <Button
                  size="sm" variant="light-brand" loading={restoringId === v.id}
                  onClick={() => restoreVersion(v.id)} icon="feather-rotate-ccw"
                >
                  Restaurar
                </Button>
              )}
            </div>
          ))}
        </div>
      )}

      {saved && <div className="mt-3"><Alert variant="success" icon="feather-check-circle">Guardado correctamente.</Alert></div>}
      {customError && <div className="mt-3"><Alert variant="danger" icon="feather-alert-triangle">{customError}</Alert></div>}

      {isStatic && (
        <p className="fs-11 text-body-secondary mt-3 mb-0">
          El prompt guardado se usa en runtime: si hay un override guardado, el
          especialista lo lee en cada turno; si no, usa su prompt por defecto.
        </p>
      )}
      {isNew && (
        <p className="fs-11 text-body-secondary mt-3 mb-0">
          Los especialistas nuevos son puramente conversacionales (sin acciones
          de negocio, sin acceso al contenido scrapeado del sitio). El
          supervisor los considera al derivar un mensaje usando la descripción
          de arriba.
        </p>
      )}
    </Card>
  );
}
