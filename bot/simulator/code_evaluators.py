from bot.flow.agents.custom import _resolver_precio_catalogo
from bot.flow.graph import INTENTS_CON_IMAGEN, RESPUESTA_GENERICA_JSON_INVALIDO


def precio_coincide_con_catalogo(turnos: list[dict]) -> str | None:
    for turno in turnos:
        for accion in turno.get("acciones", []):
            if accion.get("nombre") != "simular_financiamiento":
                continue
            resultado = accion.get("resultado") or {}
            if not resultado.get("ok"):
                continue
            params = accion.get("params") or {}
            modelo = params.get("modelo", "")
            version = params.get("version", "")
            precio_catalogo = _resolver_precio_catalogo(modelo, version)
            if precio_catalogo is None:
                continue  # sin dato de catalogo para este modelo, no se puede verificar
            if resultado.get("precio") != precio_catalogo:
                return (
                    f"simular_financiamiento uso precio {resultado.get('precio')} para "
                    f"modelo={modelo!r} version={version!r}, pero VehiculoCatalogo indica {precio_catalogo}"
                )
    return None


def codigo_reserva_no_inventado(turnos: list[dict]) -> str | None:
    codigos_conocidos = set()
    for turno in turnos:
        for accion in turno.get("acciones", []):
            resultado = accion.get("resultado") or {}
            if resultado.get("ok") and resultado.get("codigo"):
                codigos_conocidos.add(resultado["codigo"])
    for turno in turnos:
        for accion in turno.get("acciones", []):
            nombre = accion.get("nombre")
            if nombre not in {"buscar_reserva", "reagendar_hora", "anular_hora"}:
                continue
            codigo_usado = (accion.get("params") or {}).get("codigo")
            if codigo_usado and codigo_usado not in codigos_conocidos:
                return f"{nombre} uso codigo {codigo_usado!r} que no viene de un resultado previo con ok=true"
    return None


def imagen_solo_con_intent_correcto(turnos: list[dict]) -> str | None:
    for i, turno in enumerate(turnos):
        if turno.get("imagen_enviada") and turno.get("intent") not in INTENTS_CON_IMAGEN:
            return (
                f"turno {i}: se envio la imagen de {turno.get('modelo_imagen')!r} "
                f"con intent={turno.get('intent')!r}, fuera de {sorted(INTENTS_CON_IMAGEN)}"
            )
    return None


def sin_imagen_duplicada(turnos: list[dict]) -> str | None:
    vistos = set()
    for i, turno in enumerate(turnos):
        if not turno.get("imagen_enviada"):
            continue
        slug = turno.get("modelo_imagen")
        if slug in vistos:
            return f"turno {i}: se reenvio la imagen de {slug!r}, ya se habia mandado antes en esta conversacion"
        vistos.add(slug)
    return None


def json_valido_cada_turno(turnos: list[dict]) -> str | None:
    for i, turno in enumerate(turnos):
        if turno.get("bot_responde") == RESPUESTA_GENERICA_JSON_INVALIDO:
            return f"turno {i}: el bot cayo en la respuesta generica de JSON invalido"
    return None


EVALUADORES_DE_CODIGO = [
    precio_coincide_con_catalogo,
    codigo_reserva_no_inventado,
    imagen_solo_con_intent_correcto,
    sin_imagen_duplicada,
    json_valido_cada_turno,
]


def correr_evaluadores_de_codigo(turnos: list[dict]) -> list[str]:
    fallos = []
    for evaluador in EVALUADORES_DE_CODIGO:
        fallo = evaluador(turnos)
        if fallo:
            fallos.append(f"{evaluador.__name__}: {fallo}")
    return fallos
