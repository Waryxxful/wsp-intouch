"""Hechos que el especialista comercial recibe en cada turno.

La URL, los tres modelos y la franja para proponer un contacto no pueden
depender de que el RAG devuelva algo: si la búsqueda viene vacía, el modelo
vuelve a decir que no tiene el sitio o ofrece las 22:00. El texto va con
tildes y sin voseo: el modelo imita el registro que lee.

La lectura es síncrona. Quien llama desde el grafo (async) tiene que
envolver `build_system_prompt` en `sync_to_async`: una query en el event
loop frena a los demás contactos del worker, y un hilo aparte no ve la
transacción sin commitear de un TestCase.
"""

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

# Mismo valor que admin_panel.views._BUSINESS_HOURS_DEFAULT. No se importa ese
# módulo en el turno: carga el simulador entero, y este bloque se arma en cada
# mensaje. test_contexto_turno ancla que las dos copias no se separen.
# Si la tabla está vacía se usa esto y NO se escriben filas.
_FILAS_SI_NO_HAY_HORARIO = (
    (0, "09:00", "18:00", True), (1, "09:00", "18:00", True), (2, "09:00", "18:00", True),
    (3, "09:00", "18:00", True), (4, "09:00", "18:00", True),
    (5, "09:00", "13:00", False), (6, "00:00", "00:00", False),
)

_HECHOS = """\
## CONTEXTO

Hechos disponibles aunque la base de conocimiento no devuelva nada:

- El sitio de InTouch es exactamente https://in-touch.cl. No inventes rutas /soluciones, /casos ni /contacto: no están verificadas.
- Los tres modelos de operación que existen son Humano, Híbrido y Automatizado. Para el detalle llama a "listar_modelos_operacion".
  - Humano: personas especializadas atienden la interacción completa, de principio a fin. Conviene en interacciones complejas, sensibles o de alto valor.
  - Híbrido: personas y agentes de inteligencia artificial se combinan según el proceso; un agente conversacional con IA puede llevar una parte y una persona retoma la que necesita criterio.
  - Automatizado: agentes conversacionales con inteligencia artificial atienden la interacción sin intervención humana directa. Conviene en procesos estructurados, consultas frecuentes y alto volumen.
- No existe en el catálogo un producto llamado «copiloto». Si preguntan por «IA copiloto», di que con ese nombre no está en el catálogo y ofrece agentes conversacionales con IA y el modelo híbrido. No inventes qué es un copiloto.
- Este bloque no trae precios, plazos, clientes, casos de éxito ni porcentajes. Si no está acá ni lo devolvió una herramienta, no lo afirmes."""


def bloque_contexto_turno(state: dict) -> str:
    """Bloque corto de hechos, franja, preferencia y consentimiento.

    `build_system_prompt` lo concatena en cada turno del comercial. No pega
    el RAG: una línea por modelo y la URL, nada de cifras.
    """
    filas, preferencia, texto_legal = _leer(state or {})
    return _armar(filas, preferencia, texto_legal)


def _leer(state: dict):
    from django.conf import settings

    from admin_panel.models import BusinessHours
    from bot.models import LeadInTouch, tiene_consentimiento

    filas = list(BusinessHours.objects.order_by("dia_semana").values_list(
        "dia_semana", "hora_inicio", "hora_fin", "activo"))
    if not filas:
        filas = list(_FILAS_SI_NO_HAY_HORARIO)

    preferencia = ""
    wa_id = str(state.get("wa_id") or "").strip().lstrip("+")
    if wa_id:
        lead = LeadInTouch.objects.filter(conversation__wa_id=wa_id).first()
        if lead is not None:
            preferencia = (lead.preferencia_horaria or "").replace("\n", " ").replace("\r", " ").strip()

    texto = getattr(settings, "TEXTO_CONSENTIMIENTO", "") or ""
    if not isinstance(texto, str):
        texto = str(texto)
    # Sin wa_id no hay de quién mirar el registro: no se infiere que aceptó.
    if texto.strip() and (not wa_id or tiene_consentimiento(wa_id) is not True):
        return filas, preferencia, texto
    return filas, preferencia, ""


def _armar(filas, preferencia: str, texto_legal: str) -> str:
    partes = [_HECHOS, _texto_horario(filas)]
    if preferencia:
        partes.append(
            f'Preferencia ya anotada: "{preferencia}". No la repitas completa. '
            "No digas que quedó agendada ni coordinada."
        )
    if texto_legal:
        partes.append(
            "Antes de pedir datos de contacto, di este texto tal cual, una sola "
            "vez, sin reformularlo. No infieras que el contacto ya lo aceptó.\n"
            "<<<INICIO_TEXTO_LEGAL>>>\n"
            + texto_legal
            + "\n<<<FIN_TEXTO_LEGAL>>>"
        )
    return "\n\n" + "\n\n".join(partes) + "\n"


def _hhmm(valor) -> str:
    if hasattr(valor, "strftime"):
        return valor.strftime("%H:%M")
    texto = str(valor).strip()
    if len(texto) >= 5 and texto[2] == ":":
        return texto[:5]
    return texto


def _rango(dias: list[int]) -> str:
    nombres = [_DIAS[d] for d in dias]
    if len(nombres) == 1:
        return nombres[0]
    consecutivos = all(dias[i] + 1 == dias[i + 1] for i in range(len(dias) - 1))
    if consecutivos:
        return f"{nombres[0]} a {nombres[-1]}"
    if len(nombres) == 2:
        return f"{nombres[0]} y {nombres[1]}"
    return ", ".join(nombres[:-1]) + f" y {nombres[-1]}"


def _texto_horario(filas) -> str:
    activos = []
    for dia, inicio, fin, activo in filas:
        if activo and isinstance(dia, int) and 0 <= dia <= 6:
            activos.append((dia, _hhmm(inicio), _hhmm(fin)))
    activos.sort()
    if not activos:
        return (
            "No hay días activos para proponer un contacto. No propongas una hora. "
            "Si el contacto indica una preferencia, anótala y dile que no queda "
            "agendada, que el equipo la confirma."
        )
    grupos: list[tuple[tuple[str, str], list[int]]] = []
    for dia, inicio, fin in activos:
        clave = (inicio, fin)
        if grupos and grupos[-1][0] == clave and dia == grupos[-1][1][-1] + 1:
            grupos[-1][1].append(dia)
        else:
            grupos.append((clave, [dia]))
    franja = "; ".join(
        f"{_rango(dias)}, de {inicio} a {fin}" for (inicio, fin), dias in grupos
    )
    return (
        "Horario en el que puedes proponer un contacto: "
        f"{franja} (hora de Chile). "
        "Propón solo dentro de esa franja: no propongas un día ni una hora que no "
        "figure ahí. Si el contacto insiste en otro día u otra hora, "
        "anótala como preferencia y dile que no queda agendada, que el equipo la "
        "confirma. No digas que quedó coordinada."
    )
