"""Datos de proyeccion del panel (docx S19/S20).

Todo lo de este modulo es FICTICIO y esta escrito a mano a proposito: vive en
un solo lugar para que se pueda auditar de un vistazo y para que quede escrito
que no son mediciones. Ninguna funcion de aca escribe en la base.

Los ids de las filas proyectadas son NEGATIVOS. No es estetica: las claves
primarias de Django son autoincrementales positivas, asi que un id negativo no
puede existir. Si un bug hiciera llegar uno a una vista de escritura, la
consulta no encuentra nada en vez de tocar la fila real de un cliente.

Cubre todo el panel: `dashboard()`, `leads()`, `reservas()`, `campanas()`,
`conversaciones()` y `mensajes()`.
"""
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from datetime import timedelta

from django.conf import settings

CIFRAS = {
    "conversaciones": 1284,
    "leads": 386,
    "leads_calificados": 214,
    "leads_hot": 97,
    "solicitudes_financiamiento": 73,
    "tasaciones_solicitadas": 48,
    "agendamientos": 62,
    "derivaciones_comerciales": 115,
    "ventas_simuladas": 31,
    "tiempo_respuesta_seg": 3,
}


def es_id_proyectado(pk) -> bool:
    """True si `pk` es un id de fila proyectada (entero negativo).

    Tolerante a basura a proposito: el pk llega de la URL o de un body JSON.
    """
    return isinstance(pk, int) and not isinstance(pk, bool) and pk < 0


# Ultima actividad de cada conversacion proyectada: (dias hacia atras, hora).
# Esta tabla la comparten conversaciones() (updated_at), mensajes() (hora del
# ultimo mensaje) y leads() (ultima_interaccion) -- las tres pantallas hablan
# de la misma persona y no pueden contradecirse: si Leads dice que Constanza
# escribio hace cuatro dias y su chat dice que fue hoy, la incoherencia se ve
# en vivo abriendo las dos pestanas.
#
# Las fechas se calculan sobre _date.today() en cada request y no van escritas
# a mano como las de reservas(). El motivo es operativo: la lista de chats
# trae dos filtros encendidos por defecto que el resto del panel no tiene
# ("abiertas", que deja fuera lo que no tuvo actividad en INACTIVE_DAYS dias,
# y el rango de la semana en curso). Con fechas fijas, a los pocos dias de
# escribirlas la pantalla donde se lee el dialogo en voz alta abre mostrando
# 3 conversaciones de 12 y hay que mover filtros delante del cliente.
_ACTIVIDAD = {
    -1: (0, "18:40"), -2: (0, "18:05"),
    -3: (1, "17:30"), -4: (1, "16:50"),
    -5: (2, "16:10"), -6: (2, "15:35"),
    -7: (3, "14:50"), -8: (3, "13:40"),
    -9: (4, "12:25"), -10: (4, "11:40"),
    -11: (5, "10:30"), -12: (5, "09:20"),
}


def _momento_de_actividad(conv_id: int):
    """Momento de la ultima actividad de la conversacion `conv_id`.

    Nunca cae antes del lunes de la semana en curso: el filtro de fechas de
    la lista de chats arranca en ese lunes, asi que un escalonamiento mas
    largo escondería justo las conversaciones mas viejas de la proyeccion.
    Por eso los dias hacia atras se recortan contra el dia de la semana
    (lunes -> todas hoy; jueves -> hasta 3 dias atras).
    """
    hoy = _date.today()
    dias, hora = _ACTIVIDAD[conv_id]
    momento = _datetime.combine(
        hoy - timedelta(days=min(dias, hoy.weekday())), _time.fromisoformat(hora),
    )
    return momento - _atraso_para_no_quedar_en_el_futuro()


def _atraso_para_no_quedar_en_el_futuro() -> timedelta:
    """Cuanto hay que correr TODA la tabla para que nada quede en el futuro.

    Las horas de `_ACTIVIDAD` son de tarde (la mas nueva, 18:40), asi que una
    demo de la manana abre la lista de chats con la conversacion estrella
    fechada varias horas mas tarde que el reloj de la sala. Se corre el
    conjunto entero por el mismo delta, y no cada fila por su cuenta, para no
    tocar ni el orden ni los huecos relativos entre conversaciones.
    """
    hoy = _date.today()
    lunes = _datetime.combine(hoy - timedelta(days=hoy.weekday()), _time.min)
    momentos = [
        _datetime.combine(
            hoy - timedelta(days=min(dias, hoy.weekday())), _time.fromisoformat(hora),
        )
        for dias, hora in _ACTIVIDAD.values()
    ]

    falta = max(momentos) - (_datetime.now() - timedelta(minutes=15))
    if falta <= timedelta(0):
        return timedelta(0)
    # Tope: correr el conjunto no puede empujar a la mas vieja antes del lunes,
    # porque el filtro por defecto de la lista de chats arranca ahi y la
    # escondería. Si los dos topes chocan (solo de madrugada, con la demo a
    # horas en que nadie la va a dar) gana el lunes: que se vean las 12
    # importa mas que la hora de las dos primeras.
    return min(falta, min(momentos) - lunes)


def _iso_actividad(conv_id: int) -> str:
    """La misma marca de tiempo, en el formato que devuelven las vistas."""
    return _momento_de_actividad(conv_id).strftime("%Y-%m-%dT%H:%M:00-03:00")


def _funnel():
    etapas = [
        ("conversaciones", "Conversaciones", CIFRAS["conversaciones"]),
        ("leads", "Leads generados", CIFRAS["leads"]),
        ("calificados", "Leads calificados", CIFRAS["leads_calificados"]),
        ("hot", "Oportunidades HOT", CIFRAS["leads_hot"]),
        ("ventas", "Ventas simuladas", CIFRAS["ventas_simuladas"]),
    ]
    total = etapas[0][2]
    funnel = []
    previo = None
    for stage, label, count in etapas:
        funnel.append({
            "stage": stage, "label": label, "count": count,
            "rate_step": round(count / previo, 4) if previo else None,
            "rate_total": round(count / total, 4) if stage != "conversaciones" else None,
        })
        previo = count
    return funnel


def leads() -> list[dict]:
    """20 leads de proyeccion (docx S18).

    5 HOT / 9 WARM / 6 COLD, coherente con el embudo de CIFRAS (mas WARM que
    HOT). Los primeros 12 (ids -1..-12) tienen conversacion abierta
    (conversation_id -1..-12, que produce `conversaciones()`); los ultimos 8
    no tienen conversacion (conversation_id None, ultima interaccion None),
    que tambien pasa en la realidad: alguien pregunta y no vuelve a escribir.
    Vehiculos y codigos salen de verdad de bot/fixtures/stock_cavem.csv
    (VehiculoUsado), no inventados.

    `ultima_interaccion` de esos 12 sale de _iso_actividad(), el mismo
    calculo que usa el updated_at de conversaciones(): las dos pantallas se
    muestran en la misma demo y la fecha tiene que ser la misma en las dos.
    `created_at` si queda fijo -- son fechas de agosto, siempre anteriores a
    la ultima interaccion, y muestran un lead que se genero hace semanas y
    volvio a escribir hace poco.
    """
    return [
        {
            "id": -1, "conversation_id": -1,
            "nombre": "Camila Fuentes", "telefono": "+569 ****4821",
            "email": "camila.fuentes@example.cl", "comuna": "La Reina",
            "vehiculo_interes": "Hyundai Creta 2023", "vehiculo_codigo": "US033",
            "presupuesto": 16000000, "pie_disponible": 5000000,
            "cuota_objetivo": 320000, "plazo_compra": "este mes",
            "tiene_parte_pago": True, "vehiculo_actual": "Chevrolet Sail 2016",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "alta",
            "temperatura": "HOT", "lead_score": 92,
            "proxima_accion": "Llamar para cerrar visita a sucursal",
            "resumen": "Busca SUV compacto, tiene el pie listo y entrega su Sail.",
            "ultima_interaccion": _iso_actividad(-1),
            "created_at": "2026-08-28T10:05:00-03:00",
        },
        {
            "id": -2, "conversation_id": -2,
            "nombre": "Matías Rojas", "telefono": "+569 ****1187",
            "email": "matias.rojas@example.cl", "comuna": "Providencia",
            "vehiculo_interes": "Suzuki Swift 2023", "vehiculo_codigo": "US011",
            "presupuesto": 13500000, "pie_disponible": 4000000,
            "cuota_objetivo": 260000, "plazo_compra": "proximos 15 dias",
            "tiene_parte_pago": False, "vehiculo_actual": "",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "alta",
            "temperatura": "HOT", "lead_score": 88,
            "proxima_accion": "Enviar cotizacion formal y agendar prueba de manejo",
            "resumen": "Compara el Swift con un hatchback rival, ya califico con el banco.",
            "ultima_interaccion": _iso_actividad(-2),
            "created_at": "2026-08-27T09:15:00-03:00",
        },
        {
            "id": -3, "conversation_id": -3,
            "nombre": "Javiera Morales", "telefono": "+569 ****7734",
            "email": "javiera.morales@example.cl", "comuna": "Ñuñoa",
            "vehiculo_interes": "Nissan Kicks 2023", "vehiculo_codigo": "US012",
            "presupuesto": 17000000, "pie_disponible": 6000000,
            "cuota_objetivo": 300000, "plazo_compra": "este mes",
            "tiene_parte_pago": True, "vehiculo_actual": "Nissan March 2015",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "alta",
            "temperatura": "HOT", "lead_score": 85,
            "proxima_accion": "Coordinar tasacion del March y agendar firma",
            "resumen": "Lista para comprar, solo falta tasar su vehiculo actual.",
            "ultima_interaccion": _iso_actividad(-3),
            "created_at": "2026-08-26T11:30:00-03:00",
        },
        {
            "id": -4, "conversation_id": -4,
            "nombre": "Ignacio Herrera", "telefono": "+569 ****2290",
            "email": "ignacio.herrera@example.cl", "comuna": "Las Condes",
            "vehiculo_interes": "JAC JS4 2023", "vehiculo_codigo": "US018",
            "presupuesto": 15500000, "pie_disponible": 5500000,
            "cuota_objetivo": 290000, "plazo_compra": "este mes",
            "tiene_parte_pago": False, "vehiculo_actual": "",
            "intencion": "compra", "sentimiento": "neutral", "urgencia": "alta",
            "temperatura": "HOT", "lead_score": 81,
            "proxima_accion": "Confirmar disponibilidad de color y cerrar",
            "resumen": "Definido en modelo y presupuesto, evalua color disponible.",
            "ultima_interaccion": _iso_actividad(-4),
            "created_at": "2026-08-25T14:20:00-03:00",
        },
        {
            "id": -5, "conversation_id": -5,
            "nombre": "Constanza Pizarro", "telefono": "+569 ****5563",
            "email": "constanza.pizarro@example.cl", "comuna": "Vitacura",
            "vehiculo_interes": "MG ZS 2023", "vehiculo_codigo": "US015",
            "presupuesto": 14000000, "pie_disponible": 4500000,
            "cuota_objetivo": 270000, "plazo_compra": "proximos 15 dias",
            "tiene_parte_pago": True, "vehiculo_actual": "Suzuki Alto 2014",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "media",
            "temperatura": "HOT", "lead_score": 80,
            "proxima_accion": "Enviar simulacion de cuota con parte de pago",
            "resumen": "Interesada en el ZS por equipamiento, evalua entregar su Alto.",
            "ultima_interaccion": _iso_actividad(-5),
            "created_at": "2026-08-24T09:40:00-03:00",
        },
        {
            "id": -6, "conversation_id": -6,
            "nombre": "Sebastián Vargas", "telefono": "+569 ****9012",
            "email": "sebastian.vargas@example.cl", "comuna": "Maipú",
            "vehiculo_interes": "Toyota Hilux 2021", "vehiculo_codigo": "US008",
            "presupuesto": 29000000, "pie_disponible": 8000000,
            "cuota_objetivo": None, "plazo_compra": "este trimestre",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "cotizacion", "sentimiento": "positivo", "urgencia": "media",
            "temperatura": "WARM", "lead_score": 78,
            "proxima_accion": "Enviar ficha tecnica y opciones de financiamiento",
            "resumen": "Compara Hilux vs Ranger para uso de trabajo.",
            "ultima_interaccion": _iso_actividad(-6),
            "created_at": "2026-08-23T15:00:00-03:00",
        },
        {
            "id": -7, "conversation_id": -7,
            "nombre": "Antonia Reyes", "telefono": "+569 ****3348",
            "email": "antonia.reyes@example.cl", "comuna": "La Florida",
            "vehiculo_interes": "Chevrolet Tracker 2023", "vehiculo_codigo": "US013",
            "presupuesto": 18000000, "pie_disponible": None,
            "cuota_objetivo": 310000, "plazo_compra": "este trimestre",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "cotizacion", "sentimiento": "neutral", "urgencia": "media",
            "temperatura": "WARM", "lead_score": 74,
            "proxima_accion": "Reagendar llamada, no contesto la primera vez",
            "resumen": "Pidio cotizacion pero aun no confirma presupuesto de pie.",
            "ultima_interaccion": _iso_actividad(-7),
            "created_at": "2026-08-22T10:30:00-03:00",
        },
        {
            "id": -8, "conversation_id": -8,
            "nombre": "Cristóbal Silva", "telefono": "+569 ****6621",
            "email": "cristobal.silva@example.cl", "comuna": "Puente Alto",
            "vehiculo_interes": "Hyundai Tucson 2023", "vehiculo_codigo": "US003",
            "presupuesto": 20500000, "pie_disponible": 6000000,
            "cuota_objetivo": 340000, "plazo_compra": "este trimestre",
            "tiene_parte_pago": False, "vehiculo_actual": "",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "media",
            "temperatura": "WARM", "lead_score": 70,
            "proxima_accion": "Enviar comparativo Tucson vs Sportage",
            "resumen": "Duda entre dos SUV medianos, pidio comparacion de equipamiento.",
            "ultima_interaccion": _iso_actividad(-8),
            "created_at": "2026-08-21T09:00:00-03:00",
        },
        {
            "id": -9, "conversation_id": -9,
            "nombre": "Josefa Araya", "telefono": "+569 ****4470",
            "email": "josefa.araya@example.cl", "comuna": "San Miguel",
            "vehiculo_interes": "Kia Seltos 2023", "vehiculo_codigo": "US032",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "WARM", "lead_score": 68,
            "proxima_accion": "Enviar catalogo digital completo",
            "resumen": "Recien empezando a mirar opciones, pidio informacion general.",
            "ultima_interaccion": _iso_actividad(-9),
            "created_at": "2026-08-20T16:10:00-03:00",
        },
        {
            "id": -10, "conversation_id": -10,
            "nombre": "Vicente Tapia", "telefono": "+569 ****8815",
            "email": "vicente.tapia@example.cl", "comuna": "Peñalolén",
            "vehiculo_interes": "Toyota Corolla Cross 2023", "vehiculo_codigo": "US035",
            "presupuesto": 24000000, "pie_disponible": 7000000,
            "cuota_objetivo": 400000, "plazo_compra": "este trimestre",
            "tiene_parte_pago": True, "vehiculo_actual": "Toyota Yaris 2017",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "media",
            "temperatura": "WARM", "lead_score": 64,
            "proxima_accion": "Confirmar fecha de tasacion del Yaris",
            "resumen": "Le gusta el Corolla Cross, evalua entregar su Yaris.",
            "ultima_interaccion": _iso_actividad(-10),
            "created_at": "2026-08-19T10:15:00-03:00",
        },
        {
            "id": -11, "conversation_id": -11,
            "nombre": "Martina Sepúlveda", "telefono": "+569 ****2207",
            "email": "martina.sepulveda@example.cl", "comuna": "Recoleta",
            "vehiculo_interes": "Renault Duster 2023", "vehiculo_codigo": "US024",
            "presupuesto": 16500000, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "cotizacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "WARM", "lead_score": 60,
            "proxima_accion": "Coordinar visita para definir forma de pago",
            "resumen": "Pregunto precio del Duster, aun sin definir forma de pago.",
            "ultima_interaccion": _iso_actividad(-11),
            "created_at": "2026-08-19T08:50:00-03:00",
        },
        {
            "id": -12, "conversation_id": -12,
            "nombre": "Benjamín Bravo", "telefono": "+569 ****9934",
            "email": "benjamin.bravo@example.cl", "comuna": "Independencia",
            "vehiculo_interes": "Chery Tiggo 7 Pro 2023", "vehiculo_codigo": "US016",
            "presupuesto": 17500000, "pie_disponible": 4000000,
            "cuota_objetivo": 300000, "plazo_compra": "este trimestre",
            "tiene_parte_pago": False, "vehiculo_actual": "",
            "intencion": "compra", "sentimiento": "positivo", "urgencia": "media",
            "temperatura": "WARM", "lead_score": 57,
            "proxima_accion": "Enviar video de la unidad disponible",
            "resumen": "Le intereso el Tiggo 7 Pro por su equipamiento tecnologico.",
            "ultima_interaccion": _iso_actividad(-12),
            "created_at": "2026-08-18T13:25:00-03:00",
        },
        {
            "id": -13, "conversation_id": None,
            "nombre": "Trinidad Cortés", "telefono": "+569 ****5109",
            "email": "trinidad.cortes@example.cl", "comuna": "Quilicura",
            "vehiculo_interes": "Kia Sportage 2022", "vehiculo_codigo": "US004",
            "presupuesto": 19000000, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "WARM", "lead_score": 54,
            "proxima_accion": "Enviar disponibilidad de colores",
            "resumen": "Consulto por el Sportage, sin conversacion abierta todavia.",
            "ultima_interaccion": None,
            "created_at": "2026-08-22T12:00:00-03:00",
        },
        {
            "id": -14, "conversation_id": None,
            "nombre": "Gabriel Riquelme", "telefono": "+569 ****6642",
            "email": "gabriel.riquelme@example.cl", "comuna": "Huechuraba",
            "vehiculo_interes": "Volkswagen Tiguan 2021", "vehiculo_codigo": "US006",
            "presupuesto": 18500000, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "WARM", "lead_score": 51,
            "proxima_accion": "Reintentar contacto por WhatsApp",
            "resumen": "Escribio una vez preguntando stock del Tiguan, no volvio.",
            "ultima_interaccion": None,
            "created_at": "2026-08-21T09:20:00-03:00",
        },
        {
            "id": -15, "conversation_id": None,
            "nombre": "Fernanda Espinoza", "telefono": "+569 ****3381",
            "email": "fernanda.espinoza@example.cl", "comuna": "La Cisterna",
            "vehiculo_interes": "Volkswagen T-Cross 2023", "vehiculo_codigo": "US036",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "COLD", "lead_score": 48,
            "proxima_accion": "Enviar recordatorio en dos semanas",
            "resumen": "Pregunto precio referencial, no respondio mas mensajes.",
            "ultima_interaccion": None,
            "created_at": "2026-08-20T11:10:00-03:00",
        },
        {
            "id": -16, "conversation_id": None,
            "nombre": "Tomás Carrasco", "telefono": "+569 ****7756",
            "email": "tomas.carrasco@example.cl", "comuna": "San Bernardo",
            "vehiculo_interes": "Toyota RAV4 2023", "vehiculo_codigo": "US001",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "COLD", "lead_score": 42,
            "proxima_accion": "Enviar catalogo general por si retoma interes",
            "resumen": "Consulto disponibilidad del RAV4, la conversacion quedo sin cierre.",
            "ultima_interaccion": None,
            "created_at": "2026-08-19T15:30:00-03:00",
        },
        {
            "id": -17, "conversation_id": None,
            "nombre": "Francisca Soto", "telefono": "+569 ****1123",
            "email": "francisca.soto@example.cl", "comuna": "Estación Central",
            "vehiculo_interes": "Mazda CX-5 2022", "vehiculo_codigo": "US002",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "COLD", "lead_score": 38,
            "proxima_accion": "Sin seguimiento por ahora",
            "resumen": "Solo pidio fotos del CX-5, no continuo la conversacion.",
            "ultima_interaccion": None,
            "created_at": "2026-08-19T09:45:00-03:00",
        },
        {
            "id": -18, "conversation_id": None,
            "nombre": "Diego Muñoz", "telefono": "+569 ****4498",
            "email": "diego.munoz@example.cl", "comuna": "Renca",
            "vehiculo_interes": "Jeep Compass 2022", "vehiculo_codigo": "US023",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "negativo", "urgencia": "baja",
            "temperatura": "COLD", "lead_score": 32,
            "proxima_accion": "Descartar si no responde en 30 dias",
            "resumen": "Encontro el auto caro a primera vista y no siguio la conversacion.",
            "ultima_interaccion": None,
            "created_at": "2026-08-18T17:00:00-03:00",
        },
        {
            "id": -19, "conversation_id": None,
            "nombre": "Valentina Castro", "telefono": "+569 ****8867",
            "email": "valentina.castro@example.cl", "comuna": "Macul",
            "vehiculo_interes": "Honda CR-V 2021", "vehiculo_codigo": "US030",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "COLD", "lead_score": 27,
            "proxima_accion": "Sin seguimiento por ahora",
            "resumen": "Pregunto por curiosidad, no busca comprar en el corto plazo.",
            "ultima_interaccion": None,
            "created_at": "2026-08-18T10:15:00-03:00",
        },
        {
            "id": -20, "conversation_id": None,
            "nombre": "Felipe Contreras", "telefono": "+569 ****2256",
            "email": "felipe.contreras@example.cl", "comuna": "Lo Barnechea",
            "vehiculo_interes": "Mercedes-Benz Clase C 2021", "vehiculo_codigo": "US009",
            "presupuesto": None, "pie_disponible": None,
            "cuota_objetivo": None, "plazo_compra": "sin definir",
            "tiene_parte_pago": None, "vehiculo_actual": "",
            "intencion": "informacion", "sentimiento": "neutral", "urgencia": "baja",
            "temperatura": "COLD", "lead_score": 22,
            "proxima_accion": "Sin seguimiento por ahora",
            "resumen": "Consulto precio del Clase C, comento que le parecia alto y no continuo la conversacion.",
            "ultima_interaccion": None,
            "created_at": "2026-08-18T08:30:00-03:00",
        },
    ]


def reservas() -> list[dict]:
    """9 horas de taller de proyeccion (docx S11).

    Reserva no se identifica por id numerico sino por `codigo` (string), asi
    que aca la convencion de ids negativos no aplica: los codigos llevan
    prefijo DEMO- en vez de eso. La DEMO-7009 es la que agenda en vivo la
    conversacion -10 de conversaciones(), con el mismo servicio, dia y hora
    que dice ese dialogo. "Cavem La Reina" es la unica sucursal del
    seed y los 8 servicios son los reales (uno de cada uno, para mostrar el
    catalogo completo). Vehiculo y kilometraje son coherentes con el
    servicio agendado (una mantencion de 10.000 km no le corresponde a un
    auto con 90.000 km recorridos). 8 reservas activas y 1 cancelada, para
    que la pantalla muestre los dos estados.
    """
    return [
        {
            "codigo": "DEMO-7001", "cliente": "Andrea Muñoz",
            "telefono": "+569 ****2210", "email": "amunoz@example.cl",
            "vehiculo": "Toyota RAV4", "patente": "BXRT-58",
            "vehiculo_anio": 2024, "vehiculo_km": 9200,
            "servicio": "Mantencion 10.000 km", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-03", "hora": "09:00", "estado": "activa",
            "created_at": "2026-08-29T10:15:00-03:00",
        },
        {
            "codigo": "DEMO-7002", "cliente": "Francisco Lagos",
            "telefono": "+569 ****4487", "email": "flagos@example.cl",
            "vehiculo": "Kia Rio", "patente": "CPLM-27",
            "vehiculo_anio": 2023, "vehiculo_km": 19800,
            "servicio": "Mantencion 20.000 km", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-03", "hora": "14:00", "estado": "activa",
            "created_at": "2026-08-29T16:40:00-03:00",
        },
        {
            "codigo": "DEMO-7003", "cliente": "Valentina Reyes",
            "telefono": "+569 ****3392", "email": "vreyes@example.cl",
            "vehiculo": "Mazda 3", "patente": "FZKB-63",
            "vehiculo_anio": 2021, "vehiculo_km": 46500,
            "servicio": "Cambio de aceite y filtro", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-04", "hora": "10:30", "estado": "activa",
            "created_at": "2026-08-30T09:05:00-03:00",
        },
        {
            "codigo": "DEMO-7004", "cliente": "Cristián Pizarro",
            "telefono": "+569 ****7761", "email": "cpizarro@example.cl",
            "vehiculo": "Hyundai Accent", "patente": "GTHN-91",
            "vehiculo_anio": 2019, "vehiculo_km": 68200,
            "servicio": "Diagnostico electronico", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-04", "hora": "16:00", "estado": "activa",
            "created_at": "2026-08-30T18:20:00-03:00",
        },
        {
            # La agenda la conversacion -10 (Vicente Tapia) en su ultimo
            # turno: el chat entrega este codigo y esta pantalla lo muestra.
            # Que la hora agendada en el dialogo aparezca despues en
            # Agendamientos es medio punto de la demo; sin esta fila, el
            # codigo que el bot dice en pantalla no existe en ninguna parte.
            # Sin email a proposito: en esa conversacion el bot pide solo el
            # nombre (el email es opcional en el prompt de agendamiento) y
            # aca no se inventa un dato que el cliente nunca dio.
            "codigo": "DEMO-7009", "cliente": "Vicente Tapia",
            "telefono": "+569 ****8815", "email": "",
            "vehiculo": "Toyota Yaris", "patente": "SKDT-52",
            "vehiculo_anio": 2017, "vehiculo_km": 98000,
            "servicio": "Mantencion 20.000 km", "sucursal": "Cavem La Reina",
            # 2026-09-08 es martes, el dia que el cliente elige en el chat.
            "fecha": "2026-09-08", "hora": "09:00", "estado": "activa",
            "created_at": _iso_actividad(-10),
        },
        {
            "codigo": "DEMO-7005", "cliente": "Camila Torres",
            "telefono": "+569 ****5528", "email": "ctorres@example.cl",
            "vehiculo": "Suzuki Alto", "patente": "NPKS-41",
            "vehiculo_anio": 2014, "vehiculo_km": 112000,
            "servicio": "Revision precompra", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-08", "hora": "11:00", "estado": "activa",
            "created_at": "2026-08-31T12:00:00-03:00",
        },
        {
            "codigo": "DEMO-7006", "cliente": "Matías Godoy",
            "telefono": "+569 ****9043", "email": "mgodoy@example.cl",
            "vehiculo": "Peugeot 208", "patente": "JQWX-15",
            "vehiculo_anio": 2020, "vehiculo_km": 37400,
            "servicio": "Alineacion y balanceo", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-09", "hora": "09:30", "estado": "activa",
            "created_at": "2026-09-01T08:50:00-03:00",
        },
        {
            "codigo": "DEMO-7007", "cliente": "Josefa Alarcón",
            "telefono": "+569 ****1176", "email": "jalarcon@example.cl",
            "vehiculo": "Chevrolet Sail", "patente": "RVXQ-77",
            "vehiculo_anio": 2016, "vehiculo_km": 95300,
            "servicio": "Revision de frenos", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-10", "hora": "15:30", "estado": "cancelada",
            "created_at": "2026-08-28T14:10:00-03:00",
        },
        {
            "codigo": "DEMO-7008", "cliente": "Rodrigo Fuentes",
            "telefono": "+569 ****6634", "email": "rfuentes@example.cl",
            "vehiculo": "Subaru XV", "patente": "KDMP-34",
            "vehiculo_anio": 2018, "vehiculo_km": 58900,
            "servicio": "Sanitizacion de aire acondicionado", "sucursal": "Cavem La Reina",
            "fecha": "2026-09-12", "hora": "09:00", "estado": "activa",
            "created_at": "2026-09-01T17:30:00-03:00",
        },
    ]


def campanas() -> list[dict]:
    """Las 3 campanas del seed con metricas de proyeccion (docx S20).

    Embudo decreciente a mano (contactos >= enviados >= respuestas >= leads
    >= conversiones) con tasas verosimiles para WhatsApp en Chile: respuesta
    20-35% de los leidos (referencia de diseno, ver mas abajo). La suma de
    leads de las 3 queda bajo CIFRAS["leads"] (386): las campanas son una
    fuente de leads, no la unica. No lee ninguna tabla.

    `entregados` y `leidos` van en None a proposito, IGUAL que en el camino
    real (`Campana.metricas()` en bot/models.py): esos dos numeros los sabe
    Meta, no el bot, y se completan aparte con GET /api/admin/template-stats
    contra la Graph API, que en una demo no esta conectada. Inventarlos aca
    es exactamente lo que el guardrail del docx S15 prohibe -- y en una demo
    comercial es peor, porque la tarjeta ya dice en pantalla "una raya
    significa que todavia no hay dato, no cero"; con un numero puesto a mano
    esa frase se contradice sola delante del cliente. Las tasas de
    entrega/lectura de esta docstring se usaron solo para elegir a mano
    `respuestas` (que si es un dato propio del bot), no para rellenar estas
    dos columnas.
    """
    return [
        {
            "id": -1, "nombre": "Cyber Auto Demo",
            "campaign_type": "cyber_auto_demo",
            "template": "cyber_auto_demo_v1", "segmento": "Base histórica",
            "objetivo": "Reactivar interesados de los últimos 6 meses",
            "mensaje": (
                "Cyber Auto CAVEM: hasta 20% dcto en usados seleccionados "
                "y financiamiento preaprobado. Escribinos CYBER para ver el stock."
            ),
            "palabra_clave": "CYBER", "activa": True,
            "contactos": 620, "enviados": 610, "entregados": None,
            "leidos": None, "respuestas": 148, "leads": 96, "conversiones": 14,
        },
        {
            "id": -2, "nombre": "Renueva tu Auto",
            "campaign_type": "renueva_tu_auto",
            "template": "renueva_tu_auto_v1", "segmento": "Vehículo con 5+ años",
            "objetivo": "Ofrecer tasación y renovación del vehículo actual",
            "mensaje": (
                "¿Tu auto ya cumplió 5 años? En CAVEM te lo tasamos gratis y "
                "te ayudamos a renovarlo. Escribí RENUEVA para agendar tu tasación."
            ),
            "palabra_clave": "RENUEVA", "activa": True,
            "contactos": 540, "enviados": 525, "entregados": None,
            "leidos": None, "respuestas": 110, "leads": 72, "conversiones": 9,
        },
        {
            "id": -3, "nombre": "Servicio Tecnico",
            "campaign_type": "servicio_tecnico_mantencion",
            "template": "servicio_tecnico_mantencion_v1",
            "segmento": "Mantención próxima a vencer",
            "objetivo": "Recordar la mantención periódica y agendar hora de taller",
            "mensaje": (
                "Tu próxima mantención está cerca. Agendá tu hora en el taller "
                "CAVEM escribiendo SERVICIO y te confirmamos disponibilidad."
            ),
            "palabra_clave": "SERVICIO", "activa": True,
            "contactos": 410, "enviados": 402, "entregados": None,
            "leidos": None, "respuestas": 79, "leads": 48, "conversiones": 5,
        },
    ]


def dashboard() -> dict:
    """Vista de proyeccion del dashboard (docx S19). NO toca la base: son
    cifras de demostracion, y mezclarlas con filas reales obligaria despues a
    limpiarlas de una BD que tambien tiene las conversaciones de prueba."""
    from admin_panel.views import get_setting  # import tardio: evita import circular con views

    hoy = _date.today()
    return {
        "es_proyeccion": True,
        "conv_count": CIFRAS["conversaciones"],
        "msg_count": CIFRAS["conversaciones"] * 9,
        "msg_today": 212,
        "msg_by_role": {"user": 110, "assistant": 99, "human": 3},
        "active_24h": 87,
        "active_flows": CIFRAS["conversaciones"],
        "chart": [
            {"date": (hoy - timedelta(days=i)).isoformat(), "count": c}
            for i, c in enumerate([212, 198, 176, 189, 165, 154, 190])
        ][::-1],
        "bot_on": True,
        "connected": True,
        "phone_id": "•••• demo",
        "model": get_setting("openrouter_model_override", "") or settings.OPENROUTER_MODEL,
        "reservas_total": CIFRAS["agendamientos"],
        "reservas_today": 4,
        "reservas_week": 19,
        "human_mode_count": CIFRAS["derivaciones_comerciales"],
        "agent_distribution": [
            {"active_agent": "ventas", "count": 812},
            {"active_agent": "agendamiento", "count": 291},
            {"active_agent": "faq", "count": 181},
        ],
        "range": {"start": (hoy - timedelta(days=29)).isoformat(), "end": hoy.isoformat()},
        "funnel": _funnel(),
        "kpis": {
            "tiempo_respuesta_promedio_seg": CIFRAS["tiempo_respuesta_seg"],
            "tiempo_respuesta_p50_seg": 2,
            "pct_respondidas": 0.98,
            "pct_agendada": round(CIFRAS["agendamientos"] / CIFRAS["conversaciones"], 4),
            "templates_enviados": 9470,
            "fuera_de_horario": 0,
        },
        "funnel_evolution": [],
        "alerts": [],
        "extra_proyeccion": {
            "solicitudes_financiamiento": CIFRAS["solicitudes_financiamiento"],
            "tasaciones_solicitadas": CIFRAS["tasaciones_solicitadas"],
            "derivaciones_comerciales": CIFRAS["derivaciones_comerciales"],
        },
    }


# --- Chats: lista de conversaciones y transcripciones (docx S17/S21) -------

# Minutos entre un mensaje y el siguiente. El bot real parte cada parrafo de
# su respuesta en un mensaje de WhatsApp distinto y guarda uno por parte (ver
# _dividir_en_mensajes en bot/whatsapp/handlers.py), asi que una respuesta
# larga aparece como 2 o 3 mensajes seguidos -- las transcripciones de aca
# estan escritas asi para que se vean como las reales.
_MINUTOS_ENTRE_MENSAJES = 1


def conversaciones() -> dict:
    """12 conversaciones de proyeccion (docx S17/S21).

    Los ids son los mismos `conversation_id` que referencian los 12 primeros
    leads de leads(), y cada campo compartido (nombre, intencion, resumen,
    lead_score, temperatura) se copia del lead: las dos pantallas se muestran
    en la misma demo y una discrepancia se ve en vivo.

    `wa_id` viene ya enmascarado con el mismo formato que mask_wa_id() del
    camino real, y los ultimos 4 digitos son los del telefono del lead.
    `updated_at` sale de _iso_actividad(), el mismo calculo que alimenta la
    `ultima_interaccion` del lead correspondiente. No lee ninguna tabla.
    """

    def _fila(conv_id, wa_id, name, active_agent, stage, intencion, resumen,
              lead_score, temperatura, modo_humano=False):
        return {
            "id": conv_id, "wa_id": wa_id, "name": name,
            "active_agent": active_agent,
            "updated_at": _iso_actividad(conv_id),
            "human_mode": modo_humano, "archived": False,
            "open_incidents": 0, "stage": stage, "intencion": intencion,
            "resumen": resumen, "lead_score": lead_score,
            "temperatura": temperatura,
        }

    # Orden descendente por updated_at, el mismo que Conversation.Meta.ordering.
    items = [
        _fila(-1, "•••4821", "Camila Fuentes", "ventas", "agenda", "compra",
              "Busca SUV compacto, tiene el pie listo y entrega su Sail.", 92, "HOT"),
        _fila(-2, "•••1187", "Matías Rojas", "ventas", "cotizacion", "compra",
              "Compara el Swift con un hatchback rival, ya califico con el banco.", 88, "HOT"),
        _fila(-3, "•••7734", "Javiera Morales", "ventas", "agenda", "compra",
              "Lista para comprar, solo falta tasar su vehiculo actual.", 85, "HOT"),
        _fila(-4, "•••2290", "Ignacio Herrera", "ventas", "seguimiento", "compra",
              "Definido en modelo y presupuesto, evalua color disponible.", 81, "HOT"),
        _fila(-5, "•••5563", "Constanza Pizarro", "ventas", "simulacion", "compra",
              "Interesada en el ZS por equipamiento, evalua entregar su Alto.", 80, "HOT"),
        _fila(-6, "•••9012", "Sebastián Vargas", "ventas", "cotizacion", "cotizacion",
              "Compara Hilux vs Ranger para uso de trabajo.", 78, "WARM"),
        # La unica en modo humano: el docx cuenta 115 derivaciones comerciales
        # y esta es la que las muestra en pantalla. El ultimo mensaje es del
        # ejecutivo (role "human"), que el panel dibuja con burbuja propia.
        _fila(-7, "•••3348", "Antonia Reyes", "ventas", "seguimiento", "cotizacion",
              "Pidio cotizacion pero aun no confirma presupuesto de pie.", 74, "WARM",
              modo_humano=True),
        _fila(-8, "•••6621", "Cristóbal Silva", "ventas", "cotizacion", "compra",
              "Duda entre dos SUV medianos, pidio comparacion de equipamiento.", 70, "WARM"),
        _fila(-9, "•••4470", "Josefa Araya", "ventas", "descubrimiento", "informacion",
              "Recien empezando a mirar opciones, pidio informacion general.", 68, "WARM"),
        _fila(-10, "•••8815", "Vicente Tapia", "agendamiento", "agenda", "compra",
              "Le gusta el Corolla Cross, evalua entregar su Yaris.", 64, "WARM"),
        _fila(-11, "•••2207", "Martina Sepúlveda", "ventas", "seguimiento", "cotizacion",
              "Pregunto precio del Duster, aun sin definir forma de pago.", 60, "WARM"),
        _fila(-12, "•••9934", "Benjamín Bravo", "ventas", "simulacion", "compra",
              "Le intereso el Tiggo 7 Pro por su equipamiento tecnologico.", 57, "WARM"),
    ]
    return {"items": items, "count": len(items), "es_proyeccion": True}


# Transcripciones. Todo numero de plata que aparece aca sale de una fuente
# verificable, nunca de una estimacion a mano:
#   - precios, kilometrajes, anios y equipamiento: bot/fixtures/stock_cavem.csv
#     (columna "Precio Oferta CLP"),
#   - montos a financiar y cuotas: bot.business.ventas._simular_financiamiento_impl
#     y _simular_por_cuota_impl, corridas con esos precios,
#   - valores de taller: los servicios de bot/management/commands/seed_cavem.py.
# Si manana el cliente le hace la misma pregunta al bot en vivo, la cifra
# tiene que dar igual. El bot habla como su prompt real (bot/flow/global_prompt.py
# y bot/fixtures/prompt_ventas.md): se presenta como "Auto IA", tutea, vende
# consultivo (pregunta antes de listar), cierra cada simulacion con la
# advertencia obligatoria y NUNCA compromete un monto de tasacion.
_TRANSCRIPCIONES = {
    # La conversacion estrella del docx S17. El runbook de la demo
    # (docs/DEPLOY_CAVEM.md S9) verifica en vivo dos cosas de aca: que el
    # saludo dispara preguntas de calificacion en vez de un catalogo volcado,
    # y que la Tucson con $5.000.000 de pie da $14.990.000 a financiar.
    -1: [
        ("user", "Hola, quiero cambiar mi auto y estoy buscando un SUV usado"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Te ayudo a encontrarlo."),
        ("assistant", "Para recomendarte bien y no llenarte de opciones: ¿para qué lo vas a usar principalmente, ciudad o también carretera? ¿Y tienes un presupuesto en mente?"),
        ("user", "Ciudad casi todo el tiempo, ando con dos niños chicos. Presupuesto como 16 millones"),
        ("assistant", "Perfecto. Con niños te conviene un SUV compacto: portón alto, buen espacio atrás y fácil de estacionar."),
        ("assistant", "¿Cómo lo pensabas pagar: al contado, financiado, o dejando tu auto actual en parte de pago?"),
        ("user", "Financiado. Tengo 5 millones de pie y quiero pagar como 320 mil al mes. Además tengo un Sail 2016 que quiero entregar"),
        ("assistant", "Anotado: pie de $5.000.000, cuota objetivo cercana a $320.000 y tu Chevrolet Sail 2016 en parte de pago."),
        ("assistant", "¿Tienes algún modelo en mente o prefieres que te proponga alternativas del stock?"),
        ("user", "Vi un Tucson en su página, ese me gustó"),
        ("assistant", "Sí, lo tenemos: Hyundai Tucson 2.0 AT Value 2023, 31.800 km, un dueño, código US003. Precio de oferta $19.990.000."),
        ("assistant", "Es un SUV mediano, más grande que lo que veníamos conversando. ¿Te simulo el financiamiento con tu pie de $5.000.000?"),
        ("user", "Sí, dale"),
        ("assistant", "Con el Tucson US003 a $19.990.000 y un pie de $5.000.000 (el 25%), quedan $14.990.000 a financiar."),
        ("assistant", "A 36 meses: $587.156\nA 48 meses: $487.708\nA 60 meses: $430.188\nTasa referencial 1,99% mensual."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "Uf, es bastante más de lo que quiero pagar"),
        ("assistant", "Te entiendo. Con $320.000 como referencia, el Tucson te queda alto salvo que estires mucho el plazo."),
        ("assistant", "Con esa cuota y tu pie de $5.000.000, a 60 meses te alcanza hasta unos $16.150.465. En ese rango te recomiendo dos:"),
        ("assistant", "Hyundai Creta 1.5 IVT Limited 2023, 20.300 km, $15.990.000 (US033): el de menos kilómetros y el más fácil de estacionar para el día a día."),
        ("assistant", "Nissan Kicks 1.6 Advance CVT 2023, 29.800 km, $15.990.000 (US012): mismo precio, cámara 360° y garantía más larga, 6 meses o 10.000 km."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "El Creta me gustó. ¿En cuánto queda la cuota?"),
        ("assistant", "Con el Creta US033 a $15.990.000 y tu pie de $5.000.000 quedan $10.990.000 a financiar."),
        ("assistant", "A 60 meses: $315.395, justo en la cuota que buscabas.\nA 48 meses: $357.565, si prefieres pagarlo más rápido."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "¿Y si entrego el Sail? ¿Cuánto me darían por él?"),
        ("assistant", "El monto de la tasación no te lo puedo comprometer yo: la hace un especialista con el auto a la vista."),
        ("assistant", "Lo que sí, lo que te den por el Sail se suma a tu pie y esa cuota baja. Para dejarlo registrado, ¿me confirmas el kilometraje aproximado y si tiene deuda o prenda vigente?"),
        ("user", "Tiene como 118 mil kilómetros y no tiene deuda"),
        ("assistant", "Listo, quedó registrada la solicitud de tasación del Chevrolet Sail 2016, 118.000 km, sin deuda."),
        ("assistant", "¿Te acomoda venir a verlo a Cavem La Reina, Av. Bilbao 1234? Atendemos de lunes a viernes de 08:30 a 18:00 y sábados de 09:00 a 13:00. En la misma visita tasamos el Sail."),
        ("user", "Sí, el martes en la tarde puedo"),
        ("assistant", "¿Me confirmas tu nombre para dejar la visita coordinada? Lo uso solo para que el ejecutivo te reciba con la simulación del Creta y la tasación del Sail preparadas."),
        ("user", "Camila Fuentes"),
        ("assistant", "Gracias, Camila. Dejo la visita para el martes en la tarde en Cavem La Reina y le paso tus antecedentes al ejecutivo, así no tienes que repetir nada."),
        ("assistant", "Te confirmamos la hora exacta por acá."),
    ],
    -2: [
        ("user", "Hola, vi el Suzuki Swift que tienen publicado. ¿Sigue disponible?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Sí, está disponible: Suzuki Swift 1.2 GLX CVT 2023, 22.400 km, un dueño, código US011, a $12.490.000. ¿Lo pensabas al contado o financiado?"),
        ("user", "Financiado. Tengo 4 millones de pie y ya estoy preaprobado en mi banco. Lo estoy comparando con un hatchback parecido"),
        ("assistant", "Con el Swift a $12.490.000 y un pie de $4.000.000 quedan $8.490.000 a financiar: a 48 meses son $276.227, con tasa referencial de 1,99% mensual. Para tu comparación, el Swift rinde 18,5 km/l aprox., el mejor consumo del stock hoy."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "Me sirve. ¿Puedo ir a probarlo?"),
        ("assistant", "Sí. Dejo la solicitud con un ejecutivo para coordinar la prueba de manejo en Cavem La Reina, Av. Bilbao 1234, y te enviamos la cotización formal del Swift. ¿Te acomoda que te contacten hoy mismo?"),
    ],
    -3: [
        ("user", "Hola, quiero cambiar mi March por algo más nuevo"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Te ayudo con las dos cosas: buscar tu próximo auto y registrar el March para tasación."),
        ("assistant", "¿Buscas algo del mismo porte o te gustaría subir a un SUV? ¿Y cómo lo pensabas pagar?"),
        ("user", "Un SUV chico, para ciudad. Tengo 6 millones de pie y quiero pagar como 300 mil al mes"),
        ("assistant", "Con eso te muestro dos que calzan:"),
        ("assistant", "Nissan Kicks 1.6 Advance CVT 2023, 29.800 km, $15.990.000 (US012): SUV urbano, cámara 360° y la garantía más larga de los dos, 6 meses o 10.000 km."),
        ("assistant", "Hyundai Creta 1.5 IVT Limited 2023, 20.300 km, $15.990.000 (US033): mismo precio y menos kilómetros."),
        ("user", "El Kicks, ya lo había visto en su página"),
        ("assistant", "Con el Kicks a $15.990.000 y tu pie de $6.000.000 quedan $9.990.000 a financiar.\nA 60 meses: $286.697, dentro de los $300.000 que buscas.\nA 48 meses: $325.030, si prefieres pagarlo más rápido."),
        ("assistant", "Tasa referencial 1,99% mensual. Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "¿Y el March me lo reciben en parte de pago?"),
        ("assistant", "Sí. Para pedir la tasación necesito unos datos: ¿de qué año es el March y cuántos kilómetros tiene?"),
        ("user", "Es 2015 y tiene 96 mil kilómetros"),
        ("assistant", "Anotado: Nissan March 2015, 96.000 km. ¿Me pasas la patente y me confirmas si tiene deuda o prenda vigente?"),
        ("user", "GJKS-38, y no tiene deuda"),
        ("assistant", "Gracias. Última cosa: ¿en qué comuna está el auto y cómo dirías que está su estado general?"),
        ("user", "En Ñuñoa, está bien cuidado y con las mantenciones al día"),
        ("assistant", "Quedó registrada la solicitud de tasación del Nissan March 2015, 96.000 km, patente GJKS-38, sin deuda, en Ñuñoa."),
        ("assistant", "El monto no te lo puedo adelantar: la tasación la hace un especialista con el auto a la vista. Lo que te den se suma a tu pie de $6.000.000 y baja la cuota del Kicks."),
        ("user", "Ya, ¿y cuándo puedo ir?"),
        ("assistant", "Te esperamos en Cavem La Reina, Av. Bilbao 1234, de lunes a viernes de 08:30 a 18:00 y sábados de 09:00 a 13:00."),
        ("assistant", "¿Te coordino la visita con un ejecutivo para ver el Kicks y tasar el March el mismo día?"),
        ("user", "Sí, el miércoles en la mañana"),
        ("assistant", "Listo. Le paso tus antecedentes y los del March al ejecutivo para el miércoles en la mañana, así no tienes que repetir nada. Te confirmamos por acá."),
    ],
    -4: [
        ("user", "Buenas, me interesa el JAC JS4. ¿Qué colores tienen?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Del JS4 tengo una sola unidad: JAC JS4 1.5T CVT Luxury 2023, 33.500 km, color gris, $14.490.000 (código US018). ¿El color es determinante para ti?"),
        ("user", "Prefería blanco, pero lo veo igual. ¿Cómo queda la cuota con 5,5 millones de pie?"),
        ("assistant", "Con el JS4 a $14.490.000 y un pie de $5.500.000 quedan $8.990.000 a financiar: a 48 meses son $292.494, con tasa referencial de 1,99% mensual."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "Ya, avísame si les llega uno blanco"),
        ("assistant", "Lo dejo registrado y te avisamos apenas ingrese un JS4 blanco. Mientras tanto le paso tus antecedentes a un ejecutivo para que te confirme si viene alguno en camino y te reserve la unidad gris si te decides."),
    ],
    -5: [
        ("user", "Hola, me interesa el MG ZS que tienen publicado. ¿Qué equipamiento trae?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. MG ZS 1.5 Luxury AT 2023, 19.300 km, un dueño, $13.490.000 (código US015). Trae techo panorámico, cámara, sensores y llantas de aleación."),
        ("user", "Se ve bien. Tengo 4,5 millones de pie y un Suzuki Alto 2014 que quiero entregar"),
        ("assistant", "Con el ZS a $13.490.000 y un pie de $4.500.000 quedan $8.990.000 a financiar: a 48 meses son $292.494, con tasa referencial de 1,99% mensual."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("assistant", "Si el Alto entra en parte de pago, tu pie sube y esa cuota baja. ¿Me confirmas el kilometraje aproximado del Alto?"),
        ("user", "Tiene como 104 mil"),
        ("assistant", "Anotado: Suzuki Alto 2014, 104.000 km. Queda registrada la solicitud de tasación; el monto lo define un especialista con el auto a la vista, no te lo puedo adelantar. Un ejecutivo te va a enviar la simulación actualizada con la tasación incluida."),
    ],
    -6: [
        ("user", "Hola, ando buscando una camioneta 4x4 para trabajo. ¿Tienen Hilux o Ranger?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Tengo las dos: Toyota Hilux SRV 2.8 4x4 AT 2021, 69.400 km, $27.490.000 (US008), y Ford Ranger XLT 3.2 4x4 AT 2022, 58.300 km, $25.990.000 (US007)."),
        ("assistant", "¿Para qué tipo de trabajo la necesitas y cuántos kilómetros haces al año? Con eso te digo cuál te conviene más."),
        ("user", "Reparto en regiones, harto kilómetro"),
        ("assistant", "Para ruta las dos son diésel 4x4 automáticas, con 7 airbags y garantía de 6 meses o 10.000 km. La Ranger tiene 11.100 km menos y está $1.500.000 más barata; la Hilux rinde 10,0 km/l aprox. contra 9,5 km/l aprox. de la Ranger."),
        ("user", "Mándame las fichas de las dos. Tengo unos 8 millones de pie"),
        ("assistant", "Listo, te llegan las dos fichas por acá. Con ese pie un ejecutivo te prepara además las alternativas de financiamiento de cada una, para que compares la cuota antes de decidir."),
    ],
    -7: [
        ("user", "Hola, quiero cotizar el Chevrolet Tracker"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Chevrolet Tracker 1.2T Premier AT 2023, 26.100 km, un dueño, $17.490.000 (código US013)."),
        ("assistant", "Para armarte la cotización con financiamiento necesito dos datos: ¿con cuánto pie cuentas y qué cuota mensual te acomoda?"),
        ("user", "La cuota como 310 mil. El pie lo tengo que ver todavía"),
        ("assistant", "Perfecto. Apenas tengas el pie te hago la simulación exacta, porque el monto que se financia cambia con eso."),
        ("assistant", "¿Prefieres que un ejecutivo te llame o seguimos por acá?"),
        ("user", "Llámame mejor"),
        ("assistant", "Listo, dejo la solicitud de llamada con un ejecutivo."),
        ("human", "Hola Antonia, soy Rodrigo de Cavem. Te llamé recién y no logré ubicarte. ¿Te acomoda que te llame mañana a las 11:00?"),
    ],
    -8: [
        ("user", "Hola, estoy entre el Tucson y el Sportage. ¿Cuál me recomiendas?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Tengo los dos: Hyundai Tucson 2.0 AT Value 2023, 31.800 km, un dueño, $19.990.000 (US003), y Kia Sportage 2.0 AT EX 2022, 42.100 km, dos dueños, $18.990.000 (US004)."),
        ("assistant", "El Tucson es un año más nuevo y tiene 10.300 km menos; el Sportage está $1.000.000 más barato. ¿Qué pesa más para ti, el kilometraje o el precio?"),
        ("user", "Me importa más el equipamiento. ¿Me mandas el comparativo de los dos?"),
        ("assistant", "Sí, te lo preparo y te lo envío por acá. ¿Con cuánto pie estás pensando? Con eso le agrego la cuota de cada uno."),
        ("user", "Unos 6 millones, y no quiero pasar de 340 mil al mes"),
        ("assistant", "Anotado: pie de $6.000.000 y cuota tope de $340.000. Te mando el comparativo de equipamiento con la simulación de los dos, para que veas cuál calza con esa cuota."),
    ],
    -9: [
        ("user", "Hola, recién estoy empezando a mirar. ¿Qué SUV usados tienen?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Tenemos varios, así que para no llenarte de opciones: ¿lo buscas más para ciudad o también para viajes? ¿Y tienes un rango de precio en mente?"),
        ("user", "Para ciudad. El precio no lo tengo claro todavía, pero vi el Kia Seltos en su página"),
        ("assistant", "El Seltos lo tenemos: Kia Seltos 1.6 AT EX 2023, 21.700 km, un dueño, $16.490.000 (código US032). Buena opción urbana, automático y con bajo kilometraje."),
        ("assistant", "Si recién estás mirando, ¿te mando el catálogo completo de usados para que veas todo el rango?"),
        ("user", "Sí, mándamelo"),
        ("assistant", "Listo, te llega por acá. Cuando tengas más clara la cuota que te acomoda me escribes y te hago la simulación."),
    ],
    -10: [
        ("user", "Hola, necesito hacerle la mantención a mi auto"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Te agendo la hora en el taller. ¿Qué vehículo es y de qué año?"),
        ("user", "Un Toyota Yaris 2017"),
        ("assistant", "Gracias. ¿Cuántos kilómetros tiene marcados hoy? Es el dato que define qué mantención le corresponde."),
        ("user", "Como 98 mil"),
        ("assistant", "Con ese kilometraje le corresponde la pauta mayor, que en el catálogo figura como Mantención 20.000 km: $219.900 referenciales, unas 3 horas en taller."),
        ("assistant", "Si prefieres solo el cambio de aceite y filtro, son $89.900 referenciales. Los dos valores son desde: si aparecen repuestos o trabajos extra, se te informa y autoriza antes de hacerlos."),
        ("user", "Ya, la mantención completa. ¿Tienes hora esta semana?"),
        ("assistant", "¿Me confirmas la patente del Yaris? Con eso reviso la disponibilidad."),
        ("user", "SKDT-52"),
        ("assistant", "En Cavem La Reina tengo estas horas: martes 09:00, martes 15:30 o miércoles 11:00. ¿Cuál te acomoda?"),
        ("user", "Martes a las 9"),
        ("assistant", "¿Me dices tu nombre para dejar la reserva? Lo uso solo para identificar tu hora en el taller."),
        ("user", "Vicente Tapia"),
        ("assistant", "Listo, Vicente. Hora agendada: Mantención 20.000 km, martes 09:00, en Cavem La Reina, Av. Bilbao 1234. Tu código de reserva es DEMO-7009."),
        ("assistant", "Llega 10 minutos antes con el permiso de circulación."),
        ("user", "Perfecto. Aprovechando, ¿cuánto vale un Corolla Cross usado? Estoy pensando en cambiar el Yaris"),
        ("assistant", "Tenemos uno: Toyota Corolla Cross 2.0 CVT SEG 2023, 27.400 km, un dueño, $23.290.000 (código US035)."),
        ("user", "Tengo unos 7 millones de pie, pero no quiero pasar de 400 mil de cuota"),
        ("assistant", "Con el Corolla Cross a $23.290.000 y un pie de $7.000.000 quedan $16.290.000 a financiar: a 60 meses son $467.496, sobre los $400.000 que buscas."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("assistant", "Con lo que te den por el Yaris tu pie sube y esa cuota baja, así que conviene tasarlo primero. ¿Te dejo la tasación para el mismo martes que vienes al taller?"),
        ("user", "Sí, dale"),
        ("assistant", "Quedó registrada la solicitud de tasación del Toyota Yaris 2017, 98.000 km, patente SKDT-52. El monto lo define un especialista con el auto a la vista, no te lo puedo adelantar."),
        ("assistant", "El martes te entregan la tasación y la simulación actualizada del Corolla Cross."),
    ],
    -11: [
        ("user", "Hola, ¿cuánto sale el Duster?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Renault Duster 1.3T CVT Iconic 2023, 28.600 km, un dueño, $16.490.000 (código US024). ¿Lo estás viendo al contado o con financiamiento?"),
        ("user", "Todavía no sé, primero quiero verlo"),
        ("assistant", "Dale. Está en Cavem La Reina, Av. Bilbao 1234; atendemos de lunes a viernes de 08:30 a 18:00 y sábados de 09:00 a 13:00. ¿Te coordino la visita con un ejecutivo para verlo y definir ahí la forma de pago?"),
        ("user", "Lo pienso y te aviso"),
        ("assistant", "Sin problema, queda anotado. Si quieres te escribo la próxima semana para ver si coordinamos la visita; si prefieres que no, me avisas y no te contactamos más."),
    ],
    -12: [
        ("user", "Hola, me llamó la atención el Chery Tiggo 7 Pro. ¿Qué trae de equipamiento?"),
        ("assistant", "Hola, soy Auto IA, el asistente virtual de Cavem. Chery Tiggo 7 Pro 1.5T CVT Luxury 2023, 27.600 km, un dueño, $16.490.000 (código US016). Trae techo panorámico, cámara 360°, climatizador dual, asientos eléctricos e iluminación LED."),
        ("user", "Se ve bueno. Tengo 4 millones de pie, ¿en cuánto queda la cuota?"),
        ("assistant", "Con el Tiggo 7 Pro a $16.490.000 y un pie de $4.000.000 quedan $12.490.000 a financiar: a 60 meses son $358.442, con tasa referencial de 1,99% mensual."),
        ("assistant", "Simulación referencial para efectos de demostración. Financiamiento sujeto a evaluación y condiciones de la entidad financiera."),
        ("user", "¿Tienen fotos o video de la unidad?"),
        ("assistant", "Sí, te mando el video por acá. Y si quieres bajar esos $358.442, un ejecutivo puede revisar contigo un pie mayor o un auto en parte de pago."),
    ],
}


def mensajes(conv_id: int) -> dict:
    """Transcripcion de proyeccion de una conversacion.

    Sin fila en _TRANSCRIPCIONES devuelve items vacio, igual que el camino
    real cuando una conversacion no tiene mensajes. `has_more` siempre False:
    la transcripcion completa cabe en una pagina, asi que el panel nunca pide
    la siguiente. No lee ninguna tabla.
    """
    base = _TRANSCRIPCIONES.get(conv_id, [])
    if not base:
        return {"items": [], "has_more": False, "es_proyeccion": True}

    # La conversacion termina a su hora de cierre y los mensajes van hacia
    # atras desde ahi, para que el ultimo mensaje coincida con el updated_at
    # que muestra la lista.
    fin = _momento_de_actividad(conv_id)
    items = []
    for i, (role, texto) in enumerate(base):
        instante = fin - timedelta(minutes=_MINUTOS_ENTRE_MENSAJES * (len(base) - 1 - i))
        items.append({
            # El id CRECE con el orden cronologico, igual que la pk
            # autoincremental del camino real: el panel ordena los mensajes
            # por id (mergeNewer en ChatOperatorPage.tsx) y con ids al reves
            # el dialogo se mostraria dado vuelta. abs(conv_id) * 100 deja
            # 99 mensajes por conversacion sin pisarse, y restar como maximo
            # `cantidad` (nunca 0) evita el id 0, que no es negativo y se
            # escaparia de es_id_proyectado().
            "id": -(abs(conv_id) * 100 + len(base) - i),
            "role": role,
            "content": texto,
            "created_at": instante.strftime("%Y-%m-%dT%H:%M:00-03:00"),
            "media_url": None,
            "media_type": None,
        })
    return {"items": items, "has_more": False, "es_proyeccion": True}
