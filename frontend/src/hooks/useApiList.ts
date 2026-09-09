import { useEffect, useState } from 'react';
import { apiFetch } from '../api';

export function useApiList<T extends { id: number }>(path: string) {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = () => apiFetch<T[]>(path).then(setItems).catch(console.error);

  useEffect(() => { load().finally(() => setLoading(false)); }, [path]);

  const create = async (body: unknown): Promise<boolean> => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch(path, { method: 'POST', body: JSON.stringify(body) })
      .catch(err => { ok = false; setError(err.message || 'Ocurrió un error inesperado.'); console.error(err); });
    await load();
    setSaving(false);
    return ok;
  };

  const update = async (id: number, body: unknown): Promise<boolean> => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch(`${path}/${id}`, { method: 'PUT', body: JSON.stringify(body) })
      .catch(err => { ok = false; setError(err.message || 'Ocurrió un error inesperado.'); console.error(err); });
    await load();
    setSaving(false);
    return ok;
  };

  const remove = async (id: number): Promise<boolean> => {
    setSaving(true);
    setError('');
    let ok = true;
    await apiFetch(`${path}/${id}`, { method: 'DELETE' })
      .catch(err => { ok = false; setError(err.message || 'Ocurrió un error inesperado.'); console.error(err); });
    await load();
    setSaving(false);
    return ok;
  };

  return { items, loading, saving, error, create, update, remove, reload: load };
}
