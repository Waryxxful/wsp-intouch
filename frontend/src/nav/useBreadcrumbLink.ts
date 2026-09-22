import type { MouseEvent } from 'react';
import { useHref, useNavigate } from 'react-router-dom';
import { shouldInterceptClick } from './interceptClick';

// Props de un breadcrumb de PageHeader (@duralux/ui renderiza
// `<a href={crumb.href} onClick={crumb.onClick}>`) que navega dentro del
// shell sin recargarlo. El href queda real y absoluto — "abrir en pestaña
// nueva" y el clic del medio siguen funcionando —, pero el clic izquierdo
// simple lo resuelve el router. Un <a href> suelto, en cambio, hace que el
// navegador pida la página de nuevo y el shell entero se vuelva a montar.
//
// `to` se resuelve como en <Link>: absoluto (el `basename` del remoto) o
// relativo a la ruta del componente ('..' desde "leads" es la raíz del
// remoto), no a la URL del navegador como un href relativo.
export function useBreadcrumbLink(to: string): {
  href: string;
  onClick: (e: MouseEvent<HTMLAnchorElement>) => void;
} {
  const href = useHref(to);
  const navigate = useNavigate();
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (!shouldInterceptClick(e, e.currentTarget.getAttribute('target'))) return;
    e.preventDefault();
    navigate(to);
  };
  return { href, onClick };
}
