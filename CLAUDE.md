# wsp_intouch — Asesor Comercial IA de InTouch

Bot comercial B2B. Copiado del árbol de `wsp_cavem` (revisión en `.origen-cavem`),
sin su historia de git. **Es el primer bot no automotriz del stack.**

- `CLIENTE_ACTIVO=intouch`. Las marcas `renault`/`astara`/`cavem` siguen en
  `CLIENTE_CHOICES` sólo porque la suite heredada las usa en sus fixtures --
  la suite corre con `CLIENTE_ACTIVO=renault`.
- **Al escribir tests que creen `SolucionInTouch`, `ModeloOperacion`, `Campana`,
  `PromptVersion` o `CustomSpecialist`: usar `cliente=settings.CLIENTE_ACTIVO`,
  nunca `"intouch"` fijo.** Esos modelos tienen un manager filtrado por cliente
  activo: con el valor fijo el fixture queda invisible, el test pasa aislado y
  falla dentro de la suite.
- **El dominio automotriz heredado está desregistrado, no borrado**
  (`VehiculoUsado`, `VehiculoCatalogo`, `Reserva`, `Servicio`, `Sucursal`, las
  encuestas y sus especialistas). Está fuera de `AGENTS` y sin tools bindeadas,
  así que es invisible para el ruteo. Se conserva porque su cobertura de tests
  es la que prueba las defensas de la biblia §IV.1. No agregarle datos ni
  chequeos del `doctor`.
- **El lead lo escribe el extractor DESPUÉS del envío**, no una tool. Ver el
  spec §7: `registrar_datos_lead` costaba 4,53s en el 17,3% de los turnos.
- Un solo especialista: `comercial`. Las consultas no comerciales (soporte,
  empleo, proveedores) se derivan con `crear_caso`.
- Diseño y decisiones: `docs/superpowers/specs/2026-09-09-bot-intouch-comercial-design.md`.
- Bitácora por sesión: `hilo.md`.
- **Cualquier duda de arquitectura, tecnología o latencia:
  `/home/admincrm/docs-repo/biblia_bots.md`.** Es la referencia única del stack.
  Si tocás algo que ella describe, actualizala en el mismo tramo de trabajo:
  vive en otro repo y no entra sola en tu `git add`.
- Verificación de salud: `manage.py doctor`. Solo lectura, sale con código != 0
  si hay falla. Cero fallas es el piso.
- Reglas de proceso: `docs/CONFIG.md`.

Correr los tests (`OPENROUTER_API_KEY` con cualquier valor: dos tests
construyen el cliente LLM real, que valida que la variable exista):

```bash
docker run --rm -v $PWD:/app -w /app --user 1000:1000 \
  -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings \
  -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy \
  --entrypoint python wsp_intouch-web manage.py test bot admin_panel
```
