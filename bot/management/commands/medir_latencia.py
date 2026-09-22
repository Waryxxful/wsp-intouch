"""Banco de medición de latencia reproducible, contra las trazas reales.

POR QUÉ EXISTE (/home/admincrm/docs-repo/biblia_bots.md §VI.3, prioridad ALTA):

Cuatro auditorías de latencia (2026-09-02, 09-03, 09-07 en `wsp_cavem` y
09-15/09-21 en este repo) reconstruyeron el mismo banco a mano desde Langfuse, y
cada una tuvo que redescubrir el mismo método. Este comando lo congela, para que
**una regresión de latencia se detecte en un comando en vez de en una auditoría
de un día**.

Las cinco cosas que hay que saber, y que este comando ya sabe:

1. **El proyecto de Langfuse está COMPARTIDO entre bots.** El 2026-09-21, de
   6.423 observaciones del proyecto, 6.125 eran de `wsp_cavem` (entorno `qa`) y
   sólo 298 de este bot (`development`). Medir sin filtrar por entorno da los
   números de otro bot. Por eso `--entorno` es obligatorio en la práctica y el
   informe imprime siempre qué entornos vio y cuáles descartó.

2. **Las rondas se reconstruyen desde la jerarquía del turno, no desde el
   `traceId` a secas**, porque hay trazas huérfanas: `extract-metadata` corre en
   la cola de envío, DESPUÉS de que el turno terminó y con su propio `traceId`
   raíz. Sumarla al turno infla la medición justo en el componente más caro.

3. **El turno se parte en tres tramos, y confundirlos manda a optimizar lo que
   no pesa.** CABEZA (ORM, ventana de contexto y registro de especialistas,
   antes de la primera llamada al LLM), LLM (ruteo + generaciones + tools) y
   COLA (lead, despacho al CRM, notificación y el primer POST a Meta). Medido el
   2026-09-15: cabeza 0,05s p50, cola 0,74s p50 — el turno es LLM, y los
   hallazgos sobre "trabajo repetido" en nuestro código valían ~0 segundos.

4. **La población es bimodal y el corte es "¿llamó una tool?".** Promediar las
   dos juntas esconde el único hallazgo accionable. El informe las separa
   siempre.

5. **Sin un control de llamada mínima intercalado, se le atribuye al código lo
   que es del proveedor** (biblia §III.3, ley 1: el overhead del proveedor se
   duplicó por ventana horaria EL MISMO DÍA, 1,90s → 7,98s). `--control N`
   dispara N llamadas mínimas reales ahora y las compara con la ventana medida.

Es de SOLO LECTURA sobre Langfuse y no toca la base de datos. La única acción
con efecto externo es `--control`, que gasta tokens de OpenRouter y por eso
viene apagado.

    python manage.py medir_latencia --desde 2026-09-15
    python manage.py medir_latencia --desde 2026-09-01 --entorno qa
    python manage.py medir_latencia --desde 2026-09-15 --control 5
    python manage.py medir_latencia --desde 2026-09-15 --json > medicion.json
"""
import base64
import json
import os
import statistics as st
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

# El nombre de la observación raíz de un turno de WhatsApp: la pone el
# @observe de bot/whatsapp/handlers.py::_run_graph. Es el único reloj que
# abarca de punta a punta lo que el contacto espera, y por eso es la unidad de
# medida de este comando.
TURNO = "whatsapp-turn"

# Observaciones que comparten `traceId` con el turno pero NO son parte de lo
# que el contacto espera. `extract-metadata` corre en la cola de envío, después
# del primer POST a Meta (bot/flow/extractor_metadatos.py y el spec del
# 2026-09-03): sumarla al turno es el error de medición que produjo el "7,6s"
# que motivó la auditoría del 2026-09-15 -- ese número es su media, no la del
# turno.
FUERA_DEL_TURNO = frozenset({"extract-metadata"})

# Nombres de las llamadas al LLM que sí ocurren dentro del turno. Se enumeran
# en vez de tomar "todo lo que no sea el turno" para que una observación nueva
# no entre en la cuenta sin que nadie lo decida.
LLM_DEL_TURNO = ("classify-intent", "generate-response",
                 "generate-response-especulativa", "run-business-action")

# Nombre propio para el control, para que no se mezcle con el tráfico real en
# ningún informe posterior (mismo criterio que `generate-response-especulativa`
# en bot/flow/graph.py).
NOMBRE_CONTROL = "control-llamada-minima"

_PAGINA = 100
_ESPERA_ENTRE_PAGINAS = 2.0
_REINTENTOS_429 = 8


def _instante(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _percentil(ordenados: list, fraccion: float):
    if not ordenados:
        return None
    return ordenados[min(len(ordenados) - 1, int(round(fraccion * (len(ordenados) - 1))))]


class Resumen:
    """Media, mediana, p95 y máximo de una lista de segundos.

    p95 y no sólo la media a propósito: la biblia §III.3 ley 2 dice que lo que
    importa es la latencia hasta el PRIMER mensaje, y la media esconde
    exactamente la cola que hace que un contacto abandone.
    """

    def __init__(self, valores):
        self.valores = sorted(v for v in valores if v is not None)

    def __bool__(self):
        return bool(self.valores)

    @property
    def n(self):
        return len(self.valores)

    def como_dict(self):
        if not self.valores:
            return {"n": 0}
        return {
            "n": self.n,
            "media": round(st.mean(self.valores), 2),
            "p50": round(st.median(self.valores), 2),
            "p95": round(_percentil(self.valores, 0.95), 2),
            "max": round(max(self.valores), 2),
        }

    def __str__(self):
        if not self.valores:
            return "sin datos"
        d = self.como_dict()
        return (f"n={d['n']:4d}  media={d['media']:6.2f}s  p50={d['p50']:6.2f}s  "
                f"p95={d['p95']:6.2f}s  máx={d['max']:6.2f}s")


class ClienteLangfuse:
    """Lo mínimo de la API pública de Langfuse para leer observaciones.

    Cliente propio en vez del SDK porque el SDK está pensado para ESCRIBIR
    trazas desde la aplicación; acá sólo se leen, y la paginación por cursor de
    `/api/public/v2/observations` es media docena de líneas.

    El 429 se reintenta con espera creciente: bajar una ventana de varios miles
    de observaciones lo dispara de forma rutinaria, y morir a mitad de camino
    obliga a bajar todo de nuevo.
    """

    def __init__(self, base_url, public_key, secret_key):
        self.base = base_url.rstrip("/")
        credencial = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.cabeceras = {"Authorization": f"Basic {credencial}"}

    def _get(self, ruta):
        for intento in range(_REINTENTOS_429):
            pedido = urllib.request.Request(self.base + ruta, headers=self.cabeceras)
            try:
                with urllib.request.urlopen(pedido, timeout=60) as respuesta:
                    return json.load(respuesta)
            except urllib.error.HTTPError as error:
                if error.code == 429 and intento < _REINTENTOS_429 - 1:
                    time.sleep(3 * (intento + 1))
                    continue
                if error.code in (401, 403):
                    raise CommandError(
                        f"Langfuse rechazó las credenciales ({error.code}). Revisá "
                        "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY y LANGFUSE_BASE_URL "
                        "en el .env.docker de este bot."
                    ) from error
                raise CommandError(f"Langfuse respondió {error.code} en {ruta}") from error

    def observaciones(self, desde: datetime, hasta: datetime, avisar=None):
        """Todas las observaciones de la ventana, paginando por cursor."""
        reunidas, cursor = [], None
        while True:
            parametros = {
                "limit": _PAGINA,
                "fromStartTime": desde.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "toStartTime": hasta.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fields": "core,basic,metrics,trace_context,model",
            }
            if cursor:
                parametros["cursor"] = cursor
            datos = self._get("/api/public/v2/observations?" + urllib.parse.urlencode(parametros))
            reunidas.extend(datos["data"])
            if avisar:
                avisar(len(reunidas))
            # La clave es `cursor`, NO `nextCursor`: el nombre que uno espera
            # por convención devuelve None y la paginación corta en la primera
            # página en silencio, con un informe que parece correcto.
            cursor = (datos.get("meta") or {}).get("cursor")
            if not cursor:
                return reunidas
            time.sleep(_ESPERA_ENTRE_PAGINAS)


class Turno:
    """Un turno de WhatsApp con sus tres tramos ya separados."""

    def __init__(self, raiz, hijos):
        self.raiz = raiz
        self.hijos = sorted(hijos, key=lambda h: h["startTime"])
        self.inicio = _instante(raiz["startTime"])
        self.total = raiz.get("latency")

        llamadas = [h for h in self.hijos if h["name"] in LLM_DEL_TURNO]
        self.llm = sum(h.get("latency") or 0 for h in llamadas)
        conteo = Counter(h["name"] for h in self.hijos)
        self.rondas_tool = conteo.get("run-business-action", 0)
        self.generaciones = conteo.get("generate-response", 0)
        self.ruteos = conteo.get("classify-intent", 0)

        if llamadas:
            self.cabeza = (_instante(llamadas[0]["startTime"]) - self.inicio).total_seconds()
            fin_ultima = max(_instante(h["endTime"]) for h in llamadas)
            self.cola = (_instante(raiz["endTime"]) - fin_ultima).total_seconds()
        else:
            # Turno sin ninguna llamada al LLM: camino instantáneo (saludo
            # puro, acuse trivial, modo humano). No tiene tramos que separar y
            # no debe contaminar la estadística de los que sí.
            self.cabeza = self.cola = None

    @property
    def instantaneo(self):
        return self.cabeza is None

    @property
    def reintentos(self):
        """Generaciones de más respecto de las que el flujo necesita.

        Un turno necesita una generación por cada ronda de tool, más la que
        redacta la respuesta final. Todo lo que sobre es un reintento -- que es
        como se descubrió el hallazgo grande de la biblia §III.1: 1,09
        reintentos de formato por turno, en el 95% de los turnos, por un
        contrato JSON incompatible con el tool-calling nativo.
        """
        return max(0, self.generaciones - (self.rondas_tool + 1))


class Command(BaseCommand):
    help = "Mide la latencia real del bot desde las trazas de Langfuse."

    def add_arguments(self, parser):
        parser.add_argument("--desde", required=True,
                            help="Fecha o instante ISO desde el cual medir (ej. 2026-09-15).")
        parser.add_argument("--hasta", default=None,
                            help="Fecha o instante ISO hasta el cual medir (por defecto, ahora).")
        parser.add_argument("--entorno", default=None,
                            help="Entorno de Langfuse a medir. Por defecto, "
                                 "LANGFUSE_TRACING_ENVIRONMENT. El proyecto está compartido "
                                 "entre bots: sin esto se miden las trazas de otro.")
        parser.add_argument("--control", type=int, default=0, metavar="N",
                            help="Dispara N llamadas mínimas reales al LLM para medir el "
                                 "overhead del proveedor AHORA. Gasta tokens.")
        parser.add_argument("--json", action="store_true", dest="como_json",
                            help="Emite el informe como JSON en vez de texto.")

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opciones):
        desde = self._fecha(opciones["desde"], "desde")
        hasta = self._fecha(opciones["hasta"], "hasta") if opciones["hasta"] else \
            datetime.now(timezone.utc) + timedelta(minutes=1)
        if hasta <= desde:
            raise CommandError("--hasta tiene que ser posterior a --desde.")

        entorno = opciones["entorno"] or os.environ.get("LANGFUSE_TRACING_ENVIRONMENT")
        if not entorno:
            raise CommandError(
                "No sé qué entorno medir. Pasá --entorno o definí "
                "LANGFUSE_TRACING_ENVIRONMENT. El proyecto de Langfuse está compartido "
                "entre bots y sin filtrar se miden las trazas de otro."
            )

        cliente = self._cliente()
        como_json = opciones["como_json"]
        if not como_json:
            self.stdout.write(f"Bajando observaciones de {desde:%Y-%m-%d %H:%M} a "
                              f"{hasta:%Y-%m-%d %H:%M} (UTC)...")
        crudas = cliente.observaciones(
            desde, hasta,
            avisar=None if como_json else (
                lambda n: self.stdout.write(f"  {n} observaciones...", ending="\r")),
        )
        if not como_json:
            self.stdout.write(" " * 40, ending="\r")

        entornos = Counter(o.get("environment") for o in crudas)
        obs = [o for o in crudas if o.get("environment") == entorno]
        if not obs:
            raise CommandError(
                f"No hay observaciones del entorno {entorno!r} en esa ventana. "
                f"Entornos presentes: {dict(entornos) or 'ninguno'}."
            )

        informe = self._analizar(obs, entorno, entornos, desde, hasta)
        if opciones["control"]:
            informe["control"] = self._medir_control(opciones["control"], como_json)

        if como_json:
            self.stdout.write(json.dumps(informe, indent=2, ensure_ascii=False))
        else:
            self._imprimir(informe)

    # ------------------------------------------------------------------ #

    def _fecha(self, texto, cual):
        try:
            valor = datetime.fromisoformat(texto)
        except ValueError:
            raise CommandError(f"--{cual}={texto!r} no es una fecha ISO "
                               f"(ej. 2026-09-15 o 2026-09-15T14:00:00).")
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)

    def _cliente(self):
        base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
        publica = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secreta = os.environ.get("LANGFUSE_SECRET_KEY")
        faltan = [n for n, v in (("LANGFUSE_BASE_URL", base), ("LANGFUSE_PUBLIC_KEY", publica),
                                 ("LANGFUSE_SECRET_KEY", secreta)) if not v]
        if faltan:
            raise CommandError(f"Faltan variables de entorno: {', '.join(faltan)}.")
        return ClienteLangfuse(base, publica, secreta)

    # ------------------------------------------------------------------ #

    def _analizar(self, obs, entorno, entornos, desde, hasta):
        por_traza = defaultdict(list)
        raices = {}
        for o in obs:
            if o["name"] == TURNO:
                raices[o["traceId"]] = o
            elif o["name"] not in FUERA_DEL_TURNO:
                por_traza[o["traceId"]].append(o)

        turnos = [Turno(raiz, por_traza.get(tid, [])) for tid, raiz in raices.items()]
        turnos.sort(key=lambda t: t.inicio)
        con_llm = [t for t in turnos if not t.instantaneo]

        # Sólo cuentan las observaciones que cuelgan de un turno. Las que no,
        # se informan aparte en vez de promediarse con las demás: el
        # 2026-09-21, tres corridas de la suite hechas con `docker exec` dentro
        # del contenedor (que SÍ tiene las credenciales de Langfuse, a
        # diferencia del `docker run` documentado en el CLAUDE.md de este repo)
        # dejaron 48 `run-business-action` sueltas en el proyecto. Mezcladas,
        # duplicaban el conteo de tools del turno y hacían aparecer una
        # regresión que no existía.
        en_turnos = {o["id"] for tid in raices for o in por_traza.get(tid, [])}
        huerfanas = [o for o in obs
                     if o["name"] != TURNO and o["id"] not in en_turnos]

        informe = {
            "ventana": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            "entorno": entorno,
            "entornos_presentes": {k: v for k, v in entornos.items()},
            "turnos": len(turnos),
            "turnos_instantaneos": len(turnos) - len(con_llm),
            "turno_completo": Resumen([t.total for t in con_llm]).como_dict(),
            "tramos": {
                "cabeza": Resumen([t.cabeza for t in con_llm]).como_dict(),
                "llm": Resumen([t.llm for t in con_llm]).como_dict(),
                "cola": Resumen([t.cola for t in con_llm]).como_dict(),
            },
            "por_observacion": {},
            "huerfanas": {},
            "poblaciones": {},
            "rondas": {},
            "arranque_en_frio": self._arranque_en_frio(con_llm),
        }

        por_nombre = defaultdict(list)
        for tid in raices:
            for o in por_traza.get(tid, []):
                por_nombre[o["name"]].append(o.get("latency"))
        for nombre, valores in sorted(por_nombre.items(), key=lambda kv: -len(kv[1])):
            informe["por_observacion"][nombre] = Resumen(valores).como_dict()

        sueltas = defaultdict(list)
        for o in huerfanas:
            sueltas[o["name"]].append(o.get("latency"))
        for nombre, valores in sorted(sueltas.items(), key=lambda kv: -len(kv[1])):
            informe["huerfanas"][nombre] = Resumen(valores).como_dict()

        # La bimodal: el corte que ordena toda la priorización.
        sin_tool = [t for t in con_llm if t.rondas_tool == 0]
        con_tool = [t for t in con_llm if t.rondas_tool > 0]
        informe["poblaciones"] = {
            "sin_tool": Resumen([t.total for t in sin_tool]).como_dict(),
            "con_tool": Resumen([t.total for t in con_tool]).como_dict(),
        }

        informe["rondas"] = {
            "tools_por_turno": round(
                st.mean([t.rondas_tool for t in con_llm]), 2) if con_llm else 0,
            "generaciones_por_turno": round(
                st.mean([t.generaciones for t in con_llm]), 2) if con_llm else 0,
            "reintentos_por_turno": round(
                st.mean([t.reintentos for t in con_llm]), 2) if con_llm else 0,
            "turnos_con_ruteo": sum(1 for t in con_llm if t.ruteos),
        }
        return informe

    def _arranque_en_frio(self, turnos):
        """Correlaciona la CABEZA de cada turno con la inactividad previa.

        Hallazgo del 2026-09-21: los dos únicos turnos con cabeza alta (5,92s y
        5,82s) eran los dos únicos precedidos por más de `CONN_MAX_AGE` de
        silencio; los otros 59 tuvieron 0,05s. Con tráfico esporádico eso golpea
        el PRIMER turno de casi toda conversación, que en lead gen es el que
        más importa, así que el informe lo separa en vez de dejarlo como un
        outlier de la media.
        """
        filas, anterior = [], None
        for t in turnos:
            inactividad = (t.inicio - anterior).total_seconds() if anterior else None
            filas.append({"inicio": t.inicio.isoformat(), "inactividad_s": inactividad,
                          "cabeza_s": round(t.cabeza, 2)})
            anterior = t.inicio
        cabezas = [f["cabeza_s"] for f in filas]
        if not cabezas:
            return {"turnos": 0}
        mediana = st.median(cabezas)
        # "Frío" = una cabeza desproporcionada respecto de la mediana, con un
        # piso absoluto para que en una muestra rápida y uniforme no se marque
        # todo como frío.
        umbral = max(1.0, mediana * 10)
        frios = [f for f in filas if f["cabeza_s"] >= umbral]
        return {
            "turnos": len(filas),
            "cabeza_p50_s": round(mediana, 2),
            "umbral_frio_s": round(umbral, 2),
            "turnos_frios": len(frios),
            "detalle_frios": frios,
        }

    # ------------------------------------------------------------------ #

    def _medir_control(self, cuantas, silencioso):
        """N llamadas mínimas al modelo conversacional, ahora.

        Biblia §III.3, ley 1: sin este control se le atribuye al código lo que
        es del proveedor. El prompt es deliberadamente trivial y el techo de
        salida mínimo: lo que se mide es el overhead de ida y vuelta, no la
        generación.
        """
        import asyncio

        from asgiref.sync import async_to_sync

        from bot.flow.graph import _get_llm

        async def una():
            llm = await _get_llm(reasoning={"effort": "none"})
            comienzo = time.monotonic()
            await llm.ainvoke("Responde exactamente: ok",
                              config={"run_name": NOMBRE_CONTROL})
            return time.monotonic() - comienzo

        async def todas():
            medidas = []
            for i in range(cuantas):
                try:
                    medidas.append(await una())
                except Exception as error:  # noqa: BLE001 -- el control no puede tumbar el informe
                    if not silencioso:
                        self.stderr.write(f"  control {i + 1}/{cuantas} falló: {error}")
                await asyncio.sleep(0.5)
            return medidas

        if not silencioso:
            self.stdout.write(f"Midiendo el control del proveedor ({cuantas} llamadas)...")
        return Resumen(async_to_sync(todas)()).como_dict()

    # ------------------------------------------------------------------ #

    def _imprimir(self, i):
        w = self.stdout.write
        w("")
        w(f"=== Latencia de {i['entorno']} — {i['turnos']} turnos ===")
        otros = {k: v for k, v in i["entornos_presentes"].items() if k != i["entorno"]}
        if otros:
            w(f"  (el proyecto de Langfuse está compartido: se descartaron {otros})")
        if i["turnos_instantaneos"]:
            w(f"  ({i['turnos_instantaneos']} turnos instantáneos sin LLM excluidos de los tramos)")
        w("")
        w(f"  {'TURNO COMPLETO':26s} {_R(i['turno_completo'])}")
        w("")
        w("  --- los tres tramos ---")
        for nombre, etiqueta in (("cabeza", "CABEZA (ORM/contexto)"),
                                 ("llm", "LLM    (ruteo+gen+tools)"),
                                 ("cola", "COLA   (lead/CRM/POST)")):
            w(f"  {etiqueta:26s} {_R(i['tramos'][nombre])}")
        w("")
        w("  --- la bimodal: ¿llamó una tool? ---")
        w(f"  {'sin tool':26s} {_R(i['poblaciones']['sin_tool'])}")
        w(f"  {'con tool':26s} {_R(i['poblaciones']['con_tool'])}")
        w("")
        w("  --- por observación, dentro del turno ---")
        for nombre, datos in i["por_observacion"].items():
            w(f"  {nombre:34s} {_R(datos)}")
        if i["huerfanas"]:
            w("")
            w("  --- sueltas: NO cuelgan de ningún turno, excluidas de todo lo de arriba ---")
            for nombre, datos in i["huerfanas"].items():
                nota = "" if nombre in FUERA_DEL_TURNO else "  <-- ¿corrida de tests o simulador?"
                w(f"  {nombre:34s} {_R(datos)}{nota}")
        w("")
        r = i["rondas"]
        w("  --- rondas por turno ---")
        w(f"  tools={r['tools_por_turno']}  generaciones={r['generaciones_por_turno']}  "
          f"reintentos={r['reintentos_por_turno']}  turnos con ruteo={r['turnos_con_ruteo']}")
        f = i["arranque_en_frio"]
        if f.get("turnos"):
            w("")
            w("  --- arranque en frío ---")
            w(f"  cabeza p50={f['cabeza_p50_s']}s, umbral={f['umbral_frio_s']}s, "
              f"turnos fríos={f['turnos_frios']}/{f['turnos']}")
            for fila in f["detalle_frios"]:
                inact = fila["inactividad_s"]
                inact = f"{inact:.0f}s de inactividad previa" if inact else "primero de la ventana"
                w(f"    {fila['inicio'][11:19]}  cabeza={fila['cabeza_s']}s  ({inact})")
        if "control" in i:
            w("")
            w(f"  --- control del proveedor, misma ventana horaria ---")
            w(f"  {_R(i['control'])}")
        w("")


class _R:
    """Envuelve un dict ya calculado para imprimirlo con el formato de Resumen."""

    def __init__(self, datos):
        self.datos = datos

    def __str__(self):
        d = self.datos
        if not d.get("n"):
            return "sin datos"
        return (f"n={d['n']:4d}  media={d['media']:6.2f}s  p50={d['p50']:6.2f}s  "
                f"p95={d['p95']:6.2f}s  máx={d['max']:6.2f}s")
