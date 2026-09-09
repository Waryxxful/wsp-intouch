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
    Vehiculos y codigos quedaron congelados tal como salieron de
    bot/fixtures/stock_cavem.csv (VehiculoUsado) antes de que ese fixture se
    borrara junto con el resto del dominio Cavem (ver .origen-cavem): no son
    datos inventados a mano, aunque la fuente original ya no exista en este
    repo.

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
    que dice ese dialogo. "Taller La Reina" (renombrada -- el seed que la
    creaba con la marca de Cavem se borro con el resto de ese dominio, ver
    .origen-cavem) es la unica sucursal, y los 8 servicios son los reales
    (uno de cada uno, para mostrar el catalogo completo). Vehiculo y
    kilometraje son coherentes con el
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
            "servicio": "Mantencion 10.000 km", "sucursal": "Taller La Reina",
            "fecha": "2026-09-03", "hora": "09:00", "estado": "activa",
            "created_at": "2026-08-29T10:15:00-03:00",
        },
        {
            "codigo": "DEMO-7002", "cliente": "Francisco Lagos",
            "telefono": "+569 ****4487", "email": "flagos@example.cl",
            "vehiculo": "Kia Rio", "patente": "CPLM-27",
            "vehiculo_anio": 2023, "vehiculo_km": 19800,
            "servicio": "Mantencion 20.000 km", "sucursal": "Taller La Reina",
            "fecha": "2026-09-03", "hora": "14:00", "estado": "activa",
            "created_at": "2026-08-29T16:40:00-03:00",
        },
        {
            "codigo": "DEMO-7003", "cliente": "Valentina Reyes",
            "telefono": "+569 ****3392", "email": "vreyes@example.cl",
            "vehiculo": "Mazda 3", "patente": "FZKB-63",
            "vehiculo_anio": 2021, "vehiculo_km": 46500,
            "servicio": "Cambio de aceite y filtro", "sucursal": "Taller La Reina",
            "fecha": "2026-09-04", "hora": "10:30", "estado": "activa",
            "created_at": "2026-08-30T09:05:00-03:00",
        },
        {
            "codigo": "DEMO-7004", "cliente": "Cristián Pizarro",
            "telefono": "+569 ****7761", "email": "cpizarro@example.cl",
            "vehiculo": "Hyundai Accent", "patente": "GTHN-91",
            "vehiculo_anio": 2019, "vehiculo_km": 68200,
            "servicio": "Diagnostico electronico", "sucursal": "Taller La Reina",
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
            "servicio": "Mantencion 20.000 km", "sucursal": "Taller La Reina",
            # 2026-09-08 es martes, el dia que el cliente elige en el chat.
            "fecha": "2026-09-08", "hora": "09:00", "estado": "activa",
            "created_at": _iso_actividad(-10),
        },
        {
            "codigo": "DEMO-7005", "cliente": "Camila Torres",
            "telefono": "+569 ****5528", "email": "ctorres@example.cl",
            "vehiculo": "Suzuki Alto", "patente": "NPKS-41",
            "vehiculo_anio": 2014, "vehiculo_km": 112000,
            "servicio": "Revision precompra", "sucursal": "Taller La Reina",
            "fecha": "2026-09-08", "hora": "11:00", "estado": "activa",
            "created_at": "2026-08-31T12:00:00-03:00",
        },
        {
            "codigo": "DEMO-7006", "cliente": "Matías Godoy",
            "telefono": "+569 ****9043", "email": "mgodoy@example.cl",
            "vehiculo": "Peugeot 208", "patente": "JQWX-15",
            "vehiculo_anio": 2020, "vehiculo_km": 37400,
            "servicio": "Alineacion y balanceo", "sucursal": "Taller La Reina",
            "fecha": "2026-09-09", "hora": "09:30", "estado": "activa",
            "created_at": "2026-09-01T08:50:00-03:00",
        },
        {
            "codigo": "DEMO-7007", "cliente": "Josefa Alarcón",
            "telefono": "+569 ****1176", "email": "jalarcon@example.cl",
            "vehiculo": "Chevrolet Sail", "patente": "RVXQ-77",
            "vehiculo_anio": 2016, "vehiculo_km": 95300,
            "servicio": "Revision de frenos", "sucursal": "Taller La Reina",
            "fecha": "2026-09-10", "hora": "15:30", "estado": "cancelada",
            "created_at": "2026-08-28T14:10:00-03:00",
        },
        {
            "codigo": "DEMO-7008", "cliente": "Rodrigo Fuentes",
            "telefono": "+569 ****6634", "email": "rfuentes@example.cl",
            "vehiculo": "Subaru XV", "patente": "KDMP-34",
            "vehiculo_anio": 2018, "vehiculo_km": 58900,
            "servicio": "Sanitizacion de aire acondicionado", "sucursal": "Taller La Reina",
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
            "id": -1, "nombre": "Webinar Agentes IA",
            "campaign_type": "webinar_agentes_ia",
            "template": "webinar_agentes_ia_v1", "segmento": "Base histórica",
            "objetivo": "Reactivar prospectos de los últimos 6 meses",
            "mensaje": (
                "InTouch: agentes conversacionales para WhatsApp, voz, chat y "
                "email. Escribinos WEBINAR y te reservamos un cupo para la próxima demo en vivo."
            ),
            "palabra_clave": "WEBINAR", "activa": True,
            "contactos": 620, "enviados": 610, "entregados": None,
            "leidos": None, "respuestas": 148, "leads": 96, "conversiones": 14,
        },
        {
            "id": -2, "nombre": "Diagnóstico Gratuito",
            "campaign_type": "diagnostico_gratuito",
            "template": "diagnostico_gratuito_v1", "segmento": "Sin contact center propio",
            "objetivo": "Ofrecer un diagnóstico gratuito de canales y volumen de atención",
            "mensaje": (
                "¿Tu equipo se satura respondiendo WhatsApp? En InTouch te hacemos "
                "un diagnóstico gratis de tus canales. Escribí DIAGNOSTICO para agendarlo."
            ),
            "palabra_clave": "DIAGNOSTICO", "activa": True,
            "contactos": 540, "enviados": 525, "entregados": None,
            "leidos": None, "respuestas": 110, "leads": 72, "conversiones": 9,
        },
        {
            "id": -3, "nombre": "Soporte y Control de Calidad",
            "campaign_type": "soporte_control_calidad",
            "template": "soporte_control_calidad_v1",
            "segmento": "Contrato próximo a renovar",
            "objetivo": "Recordar la revisión periódica de calidad y agendar hora con el ejecutivo",
            "mensaje": (
                "Tu próxima revisión de calidad está cerca. Agendá tu hora con tu "
                "ejecutivo InTouch escribiendo SOPORTE y te confirmamos disponibilidad."
            ),
            "palabra_clave": "SOPORTE", "activa": True,
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


# Transcripciones (datos ficticios, igual que el resto del modulo). Los
# nombres de soluciones y modelos de operacion son coherentes con el catalogo
# que describe el spec (docs/superpowers/specs/2026-09-09-bot-intouch-
# comercial-design.md §5.1): diseno de operacion a medida; agentes
# conversacionales para WhatsApp/voz/chat/email; paneles y dashboards con
# Power BI; integraciones con CRM/ERP (sujetas a evaluacion tecnica); y
# analitica conversacional y control de calidad -- mas los tres modelos de
# operacion (humano, hibrido, automatizado). El bot se presenta como
# "Asesor Comercial IA de InTouch" (identidad fijada en el spec §8), tutea,
# diagnostica la situacion de contact center/canales/volumen antes de
# recomendar, y nunca compromete un precio exacto sin pasar por un
# ejecutivo.
#
# Excepcion: la conversacion -10 no se reescribio. Es una reserva de taller
# (liga con reservas()/DEMO-7009 mas abajo) del dominio automotriz heredado,
# que quedo desregistrado pero no borrado (ver CLAUDE.md) -- se le sacaron
# solo las menciones a "Cavem"/"Auto IA", sin inventarle una identidad de
# InTouch que no le corresponde: ese especialista no es el que este bot usa.
_TRANSCRIPCIONES = {
    # Conversacion de referencia: valida que el saludo dispare preguntas de
    # diagnostico (contact center, canales, volumen) en vez de listar el
    # catalogo de soluciones de una sola vez.
    -1: [
        ("user", "Hola, andamos viendo opciones para atender mejor los pedidos que nos llegan por WhatsApp"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. Para orientarte bien: ¿hoy tienen algún equipo o sistema atendiendo esos mensajes, o los responde alguien manualmente?"),
        ("user", "Los responde una persona, pero se nos acumulan hartos en las tardes. Somos una cadena de ferreterías, como 12 locales"),
        ("assistant", "Entendido. ¿Más o menos cuántas conversaciones por WhatsApp reciben al día entre los 12 locales?"),
        ("user", "Unas 300 al día, calculo. Y también nos escriben por Instagram"),
        ("assistant", "Con ese volumen y dos canales, te recomiendo agentes conversacionales para WhatsApp e Instagram con un modelo híbrido: el bot resuelve consultas de stock y horario, y deriva a una persona cuando hay una venta grande o un reclamo."),
        ("assistant", "¿Cuál dirías que es la necesidad más urgente hoy: no perder pedidos, bajar el tiempo de respuesta, o algo de reportería para ver qué se pregunta más?"),
        ("user", "Sobre todo no perder pedidos en la tarde, se nos escapan varias ventas"),
        ("assistant", "Anotado. Con eso te arma la propuesta un ejecutivo, enfocada en el pico de la tarde. ¿Te acomoda que te contacten esta semana para una reunión de 30 minutos?"),
        ("user", "Sí, dale"),
        ("assistant", "Perfecto, Camila. Quedó agendada tu solicitud, un ejecutivo te escribe por acá para coordinar el horario."),
    ],
    -2: [
        ("user", "Hola, quiero saber si InTouch tiene algo para agilizar la atención de nuestra correduría de seguros"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. Cuéntame: ¿hoy tienen un contact center propio, externalizado, o la atención la hace directamente el equipo comercial?"),
        ("user", "Es un equipo comercial de 8 personas, sin contact center. Ya evaluamos otro proveedor pero no nos convenció"),
        ("assistant", "¿Qué fue lo que no les convenció, para no repetir lo mismo?"),
        ("user", "Que no distinguía bien entre consulta de póliza y venta nueva, mezclaba todo"),
        ("assistant", "Con ese antecedente te recomiendo un agente conversacional con diseño de operación a medida: se define primero el árbol de consultas (siniestro, renovación, cotización) antes de automatizar nada, justamente para no mezclar los flujos."),
        ("user", "Me interesa. ¿Cuánto demora implementarlo?"),
        ("assistant", "Eso lo define un ejecutivo según el volumen y los canales que sumen. ¿Te preparo una propuesta formal esta semana?"),
        ("user", "Sí, por favor"),
        ("assistant", "Listo, Matías. Dejo la solicitud con un ejecutivo para que te envíe la propuesta formal."),
    ],
    -3: [
        ("user", "Hola, tenemos una clínica dental y la recepción no da abasto respondiendo WhatsApp"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. ¿La recepción atiende solo WhatsApp o también teléfono y otros canales?"),
        ("user", "Teléfono y WhatsApp. Somos 3 sucursales y coordinamos hora ahí mismo"),
        ("assistant", "¿Ya usan algún sistema de agenda o todo es manual?"),
        ("user", "Todo manual, por eso se duplican horas a veces"),
        ("assistant", "Con eso te recomiendo un agente conversacional para WhatsApp integrado a tu sistema de agenda, modelo automatizado para coordinar horas y modelo humano solo para casos de urgencia. Evita los cruces."),
        ("user", "Suena bien. ¿Tiene panel para ver cómo va funcionando?"),
        ("assistant", "Sí, se suma un panel con dashboards para seguimiento de horas agendadas y tiempos de respuesta."),
        ("assistant", "¿Coordinamos una reunión con un ejecutivo para ver el detalle de implementación en tus 3 sucursales?"),
        ("user", "Dale, esta semana si se puede"),
        ("assistant", "Quedó registrada la solicitud, Javiera. Te contactan esta semana."),
    ],
    -4: [
        ("user", "Hola, buscamos algo para atender consultas de propiedades por WhatsApp"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. ¿Hoy cómo manejan esas consultas: cada agente inmobiliario directo, o algún sistema centralizado?"),
        ("user", "Cada corredor responde las suyas, no hay nada centralizado"),
        ("assistant", "Te recomiendo un agente conversacional con panel centralizado: responde consultas de disponibilidad y agenda visitas, y deriva al corredor asignado cuando hay intención real de compra."),
        ("user", "¿Y para reportería, para saber qué propiedades preguntan más?"),
        ("assistant", "Esa parte la cubre el módulo de analítica conversacional y control de calidad, que suma al panel."),
        ("user", "Me interesa la combinación de los dos"),
        ("assistant", "Perfecto. Con esa combinación un ejecutivo te arma la propuesta. ¿Confirmas tu cargo en la inmobiliaria para dejarlo anotado?"),
        ("user", "Soy el gerente comercial"),
        ("assistant", "Gracias, Ignacio. Quedó anotado y un ejecutivo te contacta para cerrar los detalles."),
    ],
    -5: [
        ("user", "Hola, somos una distribuidora de repuestos y queremos un canal de WhatsApp con supervisión"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. ¿Cuántas conversaciones manejan aproximadamente al mes hoy?"),
        ("user", "Unas 2.000 al mes, entre cotizaciones y consultas de stock"),
        ("assistant", "Con ese volumen te recomiendo agentes conversacionales para WhatsApp más un panel de supervisión con dashboards, para que el equipo vea en tiempo real cuántas se resuelven solas y cuántas necesitan un vendedor."),
        ("assistant", "¿Tienen hoy algún sistema de cotización que debiéramos integrar?"),
        ("user", "Sí, un ERP propio"),
        ("assistant", "Anotado. Esa parte pasa por integraciones con CRM y ERP, que llevan una evaluación técnica previa antes de cotizar el proyecto completo."),
        ("user", "Ya, ¿cómo seguimos?"),
        ("assistant", "Un ejecutivo te contacta para coordinar esa evaluación técnica y armar la propuesta con el volumen que me diste."),
    ],
    -6: [
        ("user", "Hola, andamos comparando modelos de atención para una empresa de transporte de carga"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. ¿Qué modelo tienen hoy: atención humana, algún bot, o mixto?"),
        ("user", "Todo humano por ahora, un equipo pequeño"),
        ("assistant", "¿Buscan reemplazar ese equipo o que el bot los apoye en el pico de consultas?"),
        ("user", "Que apoye, no reemplazar. El volumen sube fuerte fin de mes"),
        ("assistant", "Para eso el modelo híbrido calza mejor que el automatizado: el bot resuelve seguimiento de despacho y horarios, y el equipo humano queda para negociación de tarifas y casos complejos."),
        ("user", "Mándame más detalle de los dos modelos para comparar con el directorio"),
        ("assistant", "Te envío el detalle de ambos por acá para que lo revisen con calma."),
    ],
    -7: [
        ("user", "Hola, quiero cotizar algo para nuestra planta agroindustrial"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. Para armar la cotización necesito dos datos: ¿cuántos canales de atención usan hoy y qué necesidad es la más urgente?"),
        ("user", "Solo WhatsApp por ahora. Lo urgente es no perder pedidos de temporada alta"),
        ("assistant", "Perfecto. Apenas confirmes el volumen aproximado te armo la recomendación exacta, porque el modelo cambia según eso."),
        ("assistant", "¿Prefieres que un ejecutivo te llame o seguimos por acá?"),
        ("user", "Llámame mejor"),
        ("assistant", "Listo, dejo la solicitud de llamada con un ejecutivo."),
        ("human", "Hola Antonia, soy Rodrigo de InTouch. Te llamé recién y no logré ubicarte. ¿Te acomoda que te llame mañana a las 11:00?"),
    ],
    -8: [
        ("user", "Hola, estamos entre dos opciones: un agente conversacional o primero integrar nuestro CRM. ¿Qué recomiendan?"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. Depende de dónde está el cuello de botella hoy: ¿el problema es la atención en sí, o que la información vive separada del CRM?"),
        ("user", "Más que nada la información separada, cuesta ver el historial completo del cliente"),
        ("assistant", "Con eso te conviene partir por integraciones con CRM y ERP, que llevan evaluación técnica primero, y sumar el agente conversacional después, ya con esa base."),
        ("user", "Tiene sentido. ¿Me mandas cómo es el proceso de evaluación técnica?"),
        ("assistant", "Sí, te lo envío por acá. Con eso un ejecutivo agenda la evaluación con tu equipo técnico."),
    ],
    -9: [
        ("user", "Hola, recién estamos mirando opciones para atención por WhatsApp en nuestras tiendas"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. Como recién están mirando: ¿te muestro el catálogo completo de soluciones para que vean todo el rango?"),
        ("user", "Sí, mándamelo"),
        ("assistant", "Listo, te llega por acá. Cuando tengan más claro el volumen y los canales que usan me escribes y te armo una recomendación concreta."),
    ],
    -10: [
        ("user", "Hola, necesito hacerle la mantención a mi auto"),
        ("assistant", "Hola, te ayudo a agendar la hora en el taller. ¿Qué vehículo es y de qué año?"),
        ("user", "Un Toyota Yaris 2017"),
        ("assistant", "Gracias. ¿Cuántos kilómetros tiene marcados hoy? Es el dato que define qué mantención le corresponde."),
        ("user", "Como 98 mil"),
        ("assistant", "Con ese kilometraje le corresponde la pauta mayor, que en el catálogo figura como Mantención 20.000 km: $219.900 referenciales, unas 3 horas en taller."),
        ("assistant", "Si prefieres solo el cambio de aceite y filtro, son $89.900 referenciales. Los dos valores son desde: si aparecen repuestos o trabajos extra, se te informa y autoriza antes de hacerlos."),
        ("user", "Ya, la mantención completa. ¿Tienes hora esta semana?"),
        ("assistant", "¿Me confirmas la patente del Yaris? Con eso reviso la disponibilidad."),
        ("user", "SKDT-52"),
        ("assistant", "En Taller La Reina tengo estas horas: martes 09:00, martes 15:30 o miércoles 11:00. ¿Cuál te acomoda?"),
        ("user", "Martes a las 9"),
        ("assistant", "¿Me dices tu nombre para dejar la reserva? Lo uso solo para identificar tu hora en el taller."),
        ("user", "Vicente Tapia"),
        ("assistant", "Listo, Vicente. Hora agendada: Mantención 20.000 km, martes 09:00, en Taller La Reina, Av. Bilbao 1234. Tu código de reserva es DEMO-7009."),
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
        ("user", "Hola, ¿cuánto sale implementar un agente conversacional para nuestra concesionaria?"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. El valor depende del volumen y los canales que necesiten. ¿Hoy atienden solo WhatsApp o también web y redes?"),
        ("user", "Todavía no tengo claro eso, primero quiero entender cómo funciona"),
        ("assistant", "Dale. Te coordino una reunión con un ejecutivo para que te muestre una demo y ahí definimos canales y volumen juntos. ¿Te acomoda esta semana?"),
        ("user", "Lo pienso y te aviso"),
        ("assistant", "Sin problema, queda anotado. Si quieres te escribo la próxima semana para ver si coordinamos la reunión; si prefieres que no, me avisas y no te contactamos más."),
    ],
    -12: [
        ("user", "Hola, tenemos un call center externalizado y queremos ver si conviene sumar agentes con IA"),
        ("assistant", "Hola, soy el Asesor Comercial IA de InTouch. ¿El call center externalizado atiende todos los canales o solo teléfono?"),
        ("user", "Solo teléfono. WhatsApp y web los maneja un equipo interno chico"),
        ("assistant", "Ahí hay una oportunidad clara: agentes conversacionales para WhatsApp y web, con modelo híbrido, mientras el call center externalizado sigue con teléfono."),
        ("user", "¿Tienen algún video o demo grabada para mostrarle a mi gerencia?"),
        ("assistant", "Sí, te mando una demo grabada por acá. Y si tu gerencia quiere ver algo en vivo, un ejecutivo puede coordinar una demo en tiempo real."),
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
