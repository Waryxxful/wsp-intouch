import { useEffect, useState } from 'react';
import { apiFetch } from '../api';

export function useApiResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = () => apiFetch<T>(path).then(setData).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, [path]);

  const save = async (body: unknown): Promise<boolean> => {
    setSaving(true);
    let ok = true;
    await apiFetch(path, { method: 'POST', body: JSON.stringify(body) })
      .catch(err => { ok = false; console.error(err); });
    // Re-fetch en vez de asumir exito: si el POST fallo (sesion expirada,
    // 500), `data` debe reflejar el estado REAL del backend, no lo que el
    // operador acaba de enviar -- mismo bug encontrado y arreglado 4 veces
    // por separado en BotStatePanel/PromptPanel/LlmConfigPanel/
    // ScrapingConfigPanel antes de que este hook existiera.
    await load();
    if (ok) { setSaved(true); setTimeout(() => setSaved(false), 2000); }
    setSaving(false);
    return ok;
  };

  return { data, loading, saving, saved, save, reload: load };
}
