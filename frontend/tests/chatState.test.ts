// Tests de src/chat/chatState.ts con el runner nativo de Node (>= 22.18
// ejecuta TypeScript sin flags). Sin dependencias: el frontend no tiene
// infraestructura de tests y sumar vitest cambiaría el lockfile.
//
//   cd frontend && node --test tests/
//
// Viven fuera de src/ a propósito: tsconfig incluye sólo src y no hay
// @types/node, así que `tsc -b` no los ve.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  conversationTarget, defaultChatFilters, parseChatFilters, parseConversationId,
  whenCurrent, writeChatFilters, type ChatFilters,
} from '../src/chat/chatState.ts';

// Miércoles 2026-09-23: la semana va del lunes 21 al domingo 27.
const HOY = new Date(2026, 8, 23, 12, 0, 0);

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// --- Bug 1: respuestas obsoletas -------------------------------------------

// Reproduce la secuencia de ChatOperatorPage al cambiar de conversación: el
// efecto de la conversación abre un alcance, dispara la carga inicial, y su
// cleanup lo aborta. Con A en vuelo, el operador pasa a B; A responde tarde.
test('una respuesta de la conversación anterior no pisa a la nueva', async () => {
  let messages: string[] = [];
  const fetchA = deferred<string[]>();
  const fetchB = deferred<string[]>();

  const scopeA = new AbortController();
  const loadA = whenCurrent(scopeA.signal, fetchA.promise, d => { messages = d; });

  scopeA.abort(); // cleanup del efecto al cambiar el parámetro de ruta
  const scopeB = new AbortController();
  const loadB = whenCurrent(scopeB.signal, fetchB.promise, d => { messages = d; });

  fetchB.resolve(['b1', 'b2']);
  await loadB;
  fetchA.resolve(['a1']); // llega tarde
  await loadA;

  assert.deepEqual(messages, ['b1', 'b2']);
});

test('una respuesta ya resuelta pero aún no aplicada tampoco se aplica', async () => {
  let aplicado = false;
  const scope = new AbortController();
  // La promesa ya está resuelta: su .then queda en la cola de microtareas.
  const load = whenCurrent(scope.signal, Promise.resolve('A'), () => { aplicado = true; });
  scope.abort(); // el cambio de conversación ocurre antes de que corra
  await load;
  assert.equal(aplicado, false);
});

test('el polling de la conversación anterior no mezcla mensajes con la nueva', async () => {
  const mergeNewer = (prev: number[], incoming: number[]) =>
    [...new Set([...prev, ...incoming])].sort((a, b) => a - b);
  let messages: number[] = [];

  const scopeA = new AbortController();
  const pollA = deferred<number[]>(); // un tick del polling de A, en vuelo
  const tickA = whenCurrent(scopeA.signal, pollA.promise, d => { messages = mergeNewer(messages, d); });

  scopeA.abort();
  const scopeB = new AbortController();
  await whenCurrent(scopeB.signal, Promise.resolve([20, 21]), d => { messages = d; });

  pollA.resolve([10, 11]);
  await tickA;
  assert.deepEqual(messages, [20, 21]);
});

test('un alcance vigente aplica su respuesta', async () => {
  let valor = '';
  await whenCurrent(new AbortController().signal, Promise.resolve('ok'), v => { valor = v; });
  assert.equal(valor, 'ok');
});

test('la cancelación no se reporta como error, una falla real sí', async () => {
  const errores: unknown[] = [];
  const abortError = Object.assign(new Error('aborted'), { name: 'AbortError' });

  const abortado = new AbortController();
  abortado.abort();
  await whenCurrent(abortado.signal, Promise.reject(new Error('500')), () => {}, e => errores.push(e));
  await whenCurrent(new AbortController().signal, Promise.reject(abortError), () => {}, e => errores.push(e));
  assert.equal(errores.length, 0);

  const falla = new Error('HTTP 500');
  await whenCurrent(new AbortController().signal, Promise.reject(falla), () => {}, e => errores.push(e));
  assert.deepEqual(errores, [falla]);
});

// --- Bug 2: clic sobre el chat que ya está abierto ------------------------

test('elegir el chat que ya está abierto no navega', () => {
  assert.equal(conversationTarget({ pathname: '/intouch/chat/7', search: '' }, 7, 7), null);
});

test('elegir otro chat navega al path absoluto y conserva el filtro', () => {
  const loc = { pathname: '/intouch/chat/7', search: '?estado=todas' };
  assert.deepEqual(conversationTarget(loc, 7, 8), { pathname: '/intouch/chat/8', search: '?estado=todas' });
  assert.deepEqual(
    conversationTarget({ pathname: '/intouch/chat/', search: '' }, null, 8),
    { pathname: '/intouch/chat/8', search: '' },
  );
  assert.deepEqual(
    conversationTarget({ pathname: '/wsp/botreagenda/chat', search: '' }, null, 3),
    { pathname: '/wsp/botreagenda/chat/3', search: '' },
  );
});

test('el id de la ruta admite negativos (proyección) y rechaza basura', () => {
  assert.equal(parseConversationId('123'), 123);
  assert.equal(parseConversationId('-4'), -4);
  assert.equal(parseConversationId(undefined), null);
  assert.equal(parseConversationId('abc'), null);
  assert.equal(parseConversationId('12x'), null);
});

// --- Bug 3: el filtro de la lista vive en la URL --------------------------

test('sin parámetros el filtro es el default, con o sin conversación en la ruta', () => {
  const f = parseChatFilters(new URLSearchParams(''), HOY);
  assert.deepEqual(f, { estado: 'abiertas', desde: '2026-09-21', hasta: '2026-09-27' });
  assert.deepEqual(f, defaultChatFilters(HOY));
});

test('recargar la URL devuelve el mismo filtro que se eligió', () => {
  const elegido: ChatFilters = { estado: 'cerradas', desde: '2026-08-01', hasta: '2026-08-31' };
  const url = writeChatFilters(new URLSearchParams(''), elegido, HOY);
  assert.deepEqual(parseChatFilters(new URLSearchParams(url.toString()), HOY), elegido);
});

test('un rango vacío se distingue del default', () => {
  const sinRango: ChatFilters = { estado: 'todas', desde: '', hasta: '' };
  const url = writeChatFilters(new URLSearchParams(''), sinRango, HOY);
  assert.equal(url.toString(), 'estado=todas&desde=&hasta=');
  assert.deepEqual(parseChatFilters(url, HOY), sinRango);
});

test('los valores default no ensucian la URL y otros parámetros se conservan', () => {
  const url = writeChatFilters(new URLSearchParams('otro=1&estado=todas'), defaultChatFilters(HOY), HOY);
  assert.equal(url.toString(), 'otro=1');
});

test('valores inválidos caen al default y el rango se recorta a 62 días', () => {
  const f = parseChatFilters(new URLSearchParams('estado=x&desde=ayer&hasta=2027-12-31'), HOY);
  assert.equal(f.estado, 'abiertas');
  assert.equal(f.desde, '2026-09-21');
  assert.equal(f.hasta, '2026-11-22');
});
