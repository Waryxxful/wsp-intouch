// Lógica del chat del operador que no depende de React: los filtros de la
// lista viven en la URL, el destino de navegación entre conversaciones y la
// guarda contra respuestas obsoletas. Está separada de ChatOperatorPage para
// poder probarla con `node --test` (ver tests/chatState.test.ts) sin sumar
// dependencias al frontend.
//
// Este archivo es idéntico en wsp_cavem, wsp_intouch y wsp_pompeyo: si se
// cambia en uno, se cambia en los tres.

export type EstadoFiltro = 'abiertas' | 'cerradas' | 'todas';

export interface ChatFilters {
  estado: EstadoFiltro;
  // YYYY-MM-DD, o '' para "sin límite" de ese lado.
  desde: string;
  hasta: string;
}

const ESTADOS: readonly EstadoFiltro[] = ['abiertas', 'cerradas', 'todas'];
const FECHA = /^\d{4}-\d{2}-\d{2}$/;

function isoLocal(d: Date): string {
  // Formato YYYY-MM-DD en zona local (evita el corrimiento de toISOString, que es UTC).
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// Lunes a domingo de la semana de `today` (zona local).
export function getWeekBounds(today: Date = new Date()): { monday: string; sunday: string } {
  const dow = today.getDay(); // 0=Dom, 1=Lun, ..., 6=Sáb
  const mondayOffset = dow === 0 ? -6 : 1 - dow;
  const monday = new Date(today);
  monday.setDate(today.getDate() + mondayOffset);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  return { monday: isoLocal(monday), sunday: isoLocal(sunday) };
}

// Tope de 62 días entre `desde` y `hasta`. Si el rango lo excede, devuelve el
// `hasta` ajustado al máximo permitido.
export function clampMaxRange(desde: string, hasta: string, maxDays = 62): string {
  if (!desde || !hasta) return hasta;
  const d = new Date(desde + 'T00:00:00');
  const h = new Date(hasta + 'T00:00:00');
  const diffDays = (h.getTime() - d.getTime()) / (1000 * 60 * 60 * 24);
  if (diffDays <= maxDays) return hasta;
  const capped = new Date(d);
  capped.setDate(capped.getDate() + maxDays);
  return isoLocal(capped);
}

// Filtro por defecto: conversaciones abiertas de la semana actual. Es el mismo
// se llegue al chat navegando, recargando o por un enlace directo a /chat/:id.
export function defaultChatFilters(today: Date = new Date()): ChatFilters {
  const { monday, sunday } = getWeekBounds(today);
  return { estado: 'abiertas', desde: monday, hasta: sunday };
}

// Lee el filtro de la lista desde la URL. Un parámetro ausente vale el
// default; uno presente y vacío (`desde=`) significa "sin límite". Un valor
// inválido (URL editada a mano) cae al default en vez de romper la lista.
export function parseChatFilters(params: URLSearchParams, today: Date = new Date()): ChatFilters {
  const def = defaultChatFilters(today);
  const rawEstado = params.get('estado');
  const estado = ESTADOS.find(e => e === rawEstado) ?? def.estado;
  const fecha = (key: 'desde' | 'hasta'): string => {
    const raw = params.get(key);
    if (raw === null) return def[key];
    return raw === '' || FECHA.test(raw) ? raw : def[key];
  };
  const desde = fecha('desde');
  return { estado, desde, hasta: clampMaxRange(desde, fecha('hasta')) };
}

// Escribe el filtro en la URL conservando los demás parámetros. Los valores
// iguales al default se omiten: así la URL queda limpia y "semana actual"
// sigue siendo la semana actual si se recarga otro día.
export function writeChatFilters(
  params: URLSearchParams,
  filters: ChatFilters,
  today: Date = new Date(),
): URLSearchParams {
  const def = defaultChatFilters(today);
  const next = new URLSearchParams(params);
  for (const key of ['estado', 'desde', 'hasta'] as const) {
    if (filters[key] === def[key]) next.delete(key);
    else next.set(key, filters[key]);
  }
  return next;
}

// Id de conversación desde el parámetro de ruta. Admite negativos: el modo
// proyección usa ids negativos para sus conversaciones ficticias.
export function parseConversationId(raw: string | undefined): number | null {
  if (raw === undefined || !/^-?\d+$/.test(raw)) return null;
  return Number(raw);
}

// Destino al elegir una conversación en la barra lateral, o null si ya está
// abierta (navegar igual apilaría una entrada duplicada en el historial).
// Path absoluto, no relativo: navigate('../chat/:id') resuelve distinto según
// si ya estabas en ".../chat" o ".../chat/:id" (ej. "chat/chat/123"). Conserva
// el query string para que el filtro de la lista acompañe al operador.
export function conversationTarget(
  location: { pathname: string; search: string },
  activeId: number | null,
  id: number,
): { pathname: string; search: string } | null {
  if (id === activeId) return null;
  const base = location.pathname.replace(/\/chat(\/[^/]*)?\/?$/, '');
  return { pathname: `${base}/chat/${id}`, search: location.search };
}

export function isAbortError(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { name?: unknown }).name === 'AbortError';
}

// Aplica el resultado de `promise` sólo si `signal` sigue vigente. Cada
// conversación abierta (y cada filtro de la lista) tiene su AbortController:
// al cambiar, se aborta el anterior, y una respuesta que llega tarde — o que
// ya estaba resuelta esperando su turno en la cola de microtareas — se
// descarta en vez de pisar el estado de la conversación nueva. Los errores de
// un alcance abortado también se descartan: no son fallas, son cancelaciones.
export function whenCurrent<T>(
  signal: AbortSignal,
  promise: Promise<T>,
  apply: (value: T) => void,
  onError: (e: unknown) => void = console.error,
): Promise<void> {
  return promise.then(
    value => { if (!signal.aborted) apply(value); },
    (e: unknown) => { if (!signal.aborted && !isAbortError(e)) onError(e); },
  );
}
