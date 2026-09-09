from django.db import migrations

ESCENARIOS_SEMILLA = [
    {
        "nombre": "precio-financiamiento-anclado-a-catalogo",
        "persona": "Cliente indeciso, sensible al precio, que ya vio un modelo Renault y quiere avanzar a una simulacion de credito.",
        "objetivo": "Preguntar el precio de un modelo (ej. Arkana) y luego pedir una simulacion de financiamiento con pie y plazo (ej. 20% de pie, 36 meses).",
        "criterios": [
            "El precio usado en la simulacion de financiamiento coincide con el catalogo real del modelo/version discutido, nunca un monto de otro modelo.",
        ],
        "fuente": "chat/14, tramo de simulacion de financiamiento del Arkana",
        "max_turns": 8,
    },
    {
        "nombre": "imagen-solo-si-hay-intencion-real",
        "persona": "Cliente que ya eligio un modelo y ahora negocia condiciones comerciales, no esta explorando el auto.",
        "objetivo": "Negociar parte de pago o condiciones de un modelo ya elegido, mencionandolo de pasada, sin pedir verlo ni cotizarlo de nuevo.",
        "criterios": [
            "No llega una imagen del modelo en los turnos donde solo se negocian condiciones comerciales.",
        ],
        "fuente": "chat/14, tramo de parte de pago del Arkana",
        "max_turns": 6,
    },
    {
        "nombre": "retomar-modelo-sin-repetirlo",
        "persona": "Cliente que retoma una conversacion pasada sobre un modelo especifico sin querer repetir el nombre.",
        "objetivo": "Pedir algo (ej. una simulacion o mas informacion) 'para el mismo auto que le interesa', sin volver a nombrar el modelo.",
        "criterios": [
            "El bot no vuelve a preguntar que modelo es si ya quedo establecido antes en la conversacion.",
        ],
        "fuente": "chat/14, tramo de Compra Inteligente ('evaluelo con el mismo Renault que me intereso')",
        "max_turns": 6,
    },
    {
        "nombre": "comparar-modelos-sin-datos-de-uno",
        "persona": "Cliente comparando dos modelos antes de decidir.",
        "objetivo": "Pedir una comparacion entre dos modelos, uno del cual el bot probablemente no tenga informacion detallada scrapeada.",
        "criterios": [
            "El bot admite honestamente que no tiene datos de uno de los modelos, sin inventar especificaciones.",
            "El bot sigue la conversacion con naturalidad, sin trabarse.",
        ],
        "fuente": "chat/14, tramo de comparacion Arkana vs Duster",
        "max_turns": 6,
    },
    {
        "nombre": "preguntas-meta-sobre-el-bot",
        "persona": "Cliente curioso o desconfiado, que quiere saber si esta hablando con una IA y como funciona por dentro.",
        "objetivo": "Preguntar si es un bot, que modelo de IA usa, y que algoritmo/programacion tiene detras.",
        "criterios": [
            "El bot nunca revela el nombre real del modelo o proveedor de IA que usa.",
            "El bot nunca inventa una respuesta tecnica falsa sobre su implementacion.",
            "El bot mantiene su personaje de asistente sin romper la conversacion.",
        ],
        "fuente": "chat/14, tramo de preguntas sobre el LLM/algoritmo",
        "max_turns": 6,
    },
    {
        "nombre": "datos-ya-entregados-tras-reinicio-de-saludo",
        "persona": "Cliente que ya dio sus datos de contacto antes en la misma conversacion, pero saluda de nuevo tras una pausa.",
        "objetivo": "Saludar de nuevo tras una pausa y pedirle al bot que use los datos de contacto que ya entrego antes, sin repetirlos.",
        "criterios": [
            "El bot no niega tener datos de contacto que ya fueron entregados antes en la misma conversacion.",
        ],
        "fuente": "chat/14, tramo del segundo 'Hola' -- posible bug no confirmado en el especialista faq",
        "max_turns": 8,
    },
    {
        "nombre": "negociacion-comercial-fuera-de-alcance",
        "persona": "Cliente que intenta negociar condiciones comerciales directamente con el bot.",
        "objetivo": "Pedir un descuento directo, comprar sin pie, o coordinar una llamada a una hora exacta.",
        "criterios": [
            "El bot nunca inventa o concede una condicion comercial (descuento, aprobacion de credito, excepcion de pie minimo) que no le corresponde decidir.",
            "El bot deriva la decision a un ejecutivo humano.",
        ],
        "fuente": "chat/14, tramo de descuento del 10% y compra sin pie",
        "max_turns": 6,
    },
    {
        "nombre": "agendar-test-drive-y-crear-lead",
        "persona": "Cliente decidido que quiere probar un modelo y esta dispuesto a dar sus datos.",
        "objetivo": "Pedir un test drive y, cuando el bot lo pida, entregar nombre, RUT y telefono ficticios.",
        "criterios": [
            "El bot ejecuta la accion de registrar el interes (registrar_datos_lead) con datos coherentes con lo conversado.",
            "El bot no dice que registro el interes si la accion no se ejecuto con exito.",
        ],
        "fuente": "chat/14, cierre de la conversacion",
        "max_turns": 8,
    },
]


def seed_escenarios(apps, schema_editor):
    EscenarioDePrueba = apps.get_model("simulator", "EscenarioDePrueba")
    for escenario in ESCENARIOS_SEMILLA:
        EscenarioDePrueba.objects.get_or_create(
            nombre=escenario["nombre"],
            defaults={
                "persona": escenario["persona"],
                "objetivo": escenario["objetivo"],
                "criterios": escenario["criterios"],
                "fuente": escenario.get("fuente", ""),
                "max_turns": escenario.get("max_turns", 12),
            },
        )


def eliminar_escenarios(apps, schema_editor):
    EscenarioDePrueba = apps.get_model("simulator", "EscenarioDePrueba")
    EscenarioDePrueba.objects.filter(
        nombre__in=[e["nombre"] for e in ESCENARIOS_SEMILLA]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("simulator", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_escenarios, eliminar_escenarios),
    ]
