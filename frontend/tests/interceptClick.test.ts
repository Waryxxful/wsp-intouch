// Tests de src/nav/interceptClick.ts (qué clics del breadcrumb resuelve el
// router en vez del navegador). Mismo runner que chatState.test.ts:
//
//   cd frontend && node --test 'tests/*.test.ts'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shouldInterceptClick, type ClickLike } from '../src/nav/interceptClick.ts';

const clic = (over: Partial<ClickLike> = {}): ClickLike => ({
  button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
  defaultPrevented: false, ...over,
});

test('el clic izquierdo simple lo resuelve el router, sin recargar el shell', () => {
  assert.equal(shouldInterceptClick(clic()), true);
  assert.equal(shouldInterceptClick(clic(), '_self'), true);
});

test('con modificadores lo resuelve el navegador (pestaña, ventana, descarga)', () => {
  assert.equal(shouldInterceptClick(clic({ ctrlKey: true })), false);
  assert.equal(shouldInterceptClick(clic({ metaKey: true })), false);
  assert.equal(shouldInterceptClick(clic({ shiftKey: true })), false);
  assert.equal(shouldInterceptClick(clic({ altKey: true })), false);
});

test('el botón del medio y el derecho no se interceptan', () => {
  assert.equal(shouldInterceptClick(clic({ button: 1 })), false);
  assert.equal(shouldInterceptClick(clic({ button: 2 })), false);
});

test('un clic ya atendido o con target a otra ventana no se intercepta', () => {
  assert.equal(shouldInterceptClick(clic({ defaultPrevented: true })), false);
  assert.equal(shouldInterceptClick(clic(), '_blank'), false);
});
