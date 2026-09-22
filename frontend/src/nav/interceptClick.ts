// Qué clics sobre un <a> conviene resolver con el router del shell en vez de
// dejárselos al navegador. Separado del hook (useBreadcrumbLink.ts) para
// poder probarlo con `node --test` sin React (ver tests/interceptClick.test.ts).
//
// Este archivo es idéntico en wsp_cavem y wsp_intouch: si se cambia en uno,
// se cambia en los dos.

// Lo mínimo de un MouseEvent que hace falta mirar; un React.MouseEvent lo cumple.
export interface ClickLike {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
}

// true sólo para un clic izquierdo sin modificadores que nadie atendió
// todavía. Con ctrl/meta (pestaña nueva), shift (ventana nueva), alt
// (descarga) o el botón del medio, el navegador tiene que hacer lo suyo con
// el href absoluto; por eso el href se conserva y sólo se intercepta este caso.
// Mismo criterio que el <Link> de react-router.
export function shouldInterceptClick(e: ClickLike, target?: string | null): boolean {
  if (e.defaultPrevented) return false;
  if (e.button !== 0) return false;
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
  if (target && target !== '_self') return false;
  return true;
}
