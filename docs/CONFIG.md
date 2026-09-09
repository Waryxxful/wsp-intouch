# Config / reglas de proceso

Reglas de como trabajamos en este repo, no de configuracion tecnica. Vive
aca (no solo en la memoria de Claude) para que cualquier sesion nueva la
lea sin depender de que el usuario la repita.

## Antes de implementar una idea del usuario

Cuando el usuario propone un fix puntual (ej. "agreguemos una columna
para X"), antes de implementarlo:

1. Enumerar las alternativas razonables (incluida la idea propuesta) con
   sus tradeoffs reales -- no asumir que la primera formulacion es la mas
   optima solo porque vino del usuario.
2. Contrastar cada alternativa contra el estado actual real del sistema
   (que ya se audito/verifico en la sesion), no en abstracto.
3. Presentar la comparacion y esperar que el usuario elija, en vez de
   implementar directamente la primera idea mencionada.

Motivo (incidente real 2026-08-31): el usuario propuso agregar una
columna `condicion` para separar seminuevos de 0km en `VehiculoCatalogo`
(ver `docs/PENDIENTES.md`, seccion Astara). Se implemento de punta a
punta (modelo, migracion, scraper, logica de negocio, tests) sin mostrar
antes alternativas mas baratas (ej. filtrar por patron de URL en la query
sin columna nueva) ni confirmar que la idea seguia siendo la mejor una vez
que aparecio un hallazgo mas grande relacionado (precios en 3 tiers:
lista/contado/financiado) mientras se verificaba el fix. El usuario tuvo
que frenar la sesion para pedir la evaluacion que debio pasar antes.

## Como aplicarlo

- Ideas chicas y reversibles (ej. un log, un test) no necesitan este paso
  completo -- usar criterio.
- Cualquier cambio de schema (migracion), de logica de negocio compartida
  (`bot/business/*`, `bot/flow/graph.py`), o que toque produccion antes de
  una prueba/demo, SI lo necesita.
- Ver tambien `docs/PENDIENTES.md` para el historial de hallazgos y fixes
  ya evaluados/resueltos.

## Antes de trabajar un pendiente (debugueo o iteracion sobre un reporte)

Regla del usuario, 2026-09-01. Aplica cada vez que vamos a trabajar un
pendiente de `docs/PENDIENTES.md` -- sea un bug que estamos debuggeando o
una iteracion sobre un hallazgo/reporte que nos mandaron (ej. la planilla
de pruebas del gerente de ventas). Antes de picar codigo:

1. **Identificar el problema en su origen real.** No asumir la causa del
   reporte -- rastrear el contexto concreto de donde salio el pendiente.
   Si es un bug del chat de WhatsApp, ubicar el mensaje/turno/conversacion
   real (BD y/o Langfuse) al que se refiere, no trabajar sobre la
   descripcion en abstracto.
2. **Explicarle al usuario el problema y por que se genero** (causa raiz
   verificada contra el codigo/datos reales, no la primera hipotesis) antes
   de proponer nada.
3. **Buscar soluciones ya creadas** para este tipo de problema, o
   tecnologias que ayuden a resolverlo -- normalmente alcanza con una
   busqueda web (documentacion oficial de la libreria/servicio involucrado,
   no solo memoria).
4. **Proponer la solucion mas optima** que se haya encontrado.
5. **Nunca proponer un parche** -- siempre una solucion completa (misma
   idea que la seccion de arriba, pero explicita para el caso de
   pendientes: atacar la causa raiz, no el sintoma).
6. **Esperar el visto bueno del usuario** antes de implementar: puede
   aceptar una de las soluciones propuestas, pedir otra, o dar la suya
   propia. No implementar hasta ese punto.

Este mismo flujo (identificar origen real vía Langfuse/BD -> explicar
causa raiz -> buscar la doc oficial del problema -> proponer alternativas
completas -> esperar decision) es el que se uso el 2026-09-01 para los
hallazgos de latencia (`classify-intent`/reasoning), despedidas duplicadas
y el bug de pie/financiamiento en `docs/PENDIENTES.md` -- sirve de
ejemplo concreto de como aplicarlo.

### El razonamiento se documenta, no solo la conclusion

Regla del usuario, 2026-09-01 (segunda ronda, tras el hallazgo de la ficha
tecnica PDF de Astara). No alcanza con anotar en `docs/PENDIENTES.md` "la
solucion es X" -- cada hallazgo tiene que dejar registrado el RAZONAMIENTO
completo, para que el usuario (y cualquier sesion futura) pueda aprender
del problema, no solo aplicar el resultado:

- Que solucion(es) mas baratas/obvias se consideraron y por que se
  descartaron (no alcanza con nombrarlas, hay que explicar especificamente
  por que no son la solucion completa para ESTE caso).
- Por que la solucion propuesta es la mas completa -- que causa raiz
  ataca, que la distingue de un parche.
- Que tecnologia/mecanismo ya existente en el repo se esta reusando (o por
  que hace falta algo nuevo), y como se llego a esa conclusion (que
  archivo/comando/busqueda lo confirmo).

Ejemplo real de este nivel de detalle: `docs/PENDIENTES.md`, seccion
"[URGENTE] Astara — auditoría completa de catálogo", punto 5 (ficha
tecnica PDF) -- documenta por que "priorizar el PDF igual que sucursales"
es un parche (293 PDFs compitiendo por el mismo presupuesto finito que las
293 paginas de modelo, vs. ~13 paginas de sucursal) y por que la
alternativa propuesta (fetch directo desde la misma pagina de modelo, sin
pasar por la cola BFS) es la solucion completa (no depende de presupuesto
compartido, cierra 2 gaps en un solo cambio, reusa 100% infraestructura ya
existente).
