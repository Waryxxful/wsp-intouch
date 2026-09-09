let API_BASE = '';

export function configureApi(base: string) {
  API_BASE = base.replace(/\/$/, '');
}

export function apiUrl(path: string): string {
  if (path.startsWith('http')) return path;
  return `${API_BASE}${path.startsWith('/') ? path : '/' + path}`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith('http')
    ? path
    : `${API_BASE}${path.startsWith('/') ? path : '/' + path}`;

  const res = await fetch(url, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  const contentType = res.headers.get('content-type') || '';
  const isRedirectedToLogin = res.redirected && !contentType.includes('application/json');
  if (res.status === 401 || isRedirectedToLogin) {
    window.dispatchEvent(new CustomEvent('grancrm:sessionExpired'));
    throw new Error('Sesión expirada — refrescá la página o volvé a iniciar sesión.');
  }
  if (!res.ok) {
    if (res.status === 403) throw new Error('No tenés permiso para hacer esto.');
    const body = contentType.includes('application/json') ? await res.json().catch(() => null) : null;
    throw new Error(body?.error || `HTTP ${res.status}`);
  }
  return res.json();
}
