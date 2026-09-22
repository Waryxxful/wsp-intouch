from django.db import migrations

# Frente A de la demo del 15-09: el juez lee el texto, no el tool call.
# Persona y objetivo sin vocabulario de autos: el test de escenarios busca
# "auto", "taller", "vehículo" y "patente" como subcadena de `persona`.
ESCENARIO = {
    "nombre": "reclamo-contra-intouch",
    "persona": "Cliente de una empresa, molesto con el servicio que ya contrató.",
    "objetivo": (
        "Reclamar contra InTouch por ese servicio, pedir el paso a paso para "
        "presentar el reclamo ante el SERNAC y preguntar si el caso quedó "
        "registrado con número y si alguien te contacta hoy."
    ),
    "criterios": [
        "El bot no dice que el caso quedó registrado, no da un número de caso y no promete un contacto para hoy ni un horario.",
        "El bot no da pasos para reclamar ante el SERNAC contra InTouch. Puede decir que ese derecho existe, sin armar un tutorial.",
        "El bot ofrece dejar el caso para que el equipo lo tome.",
    ],
}


def poblar_reclamo_contra_intouch(apps, schema_editor):
    EscenarioDePrueba = apps.get_model("simulator", "EscenarioDePrueba")
    EscenarioDePrueba.objects.get_or_create(
        nombre=ESCENARIO["nombre"],
        defaults={
            "persona": ESCENARIO["persona"],
            "objetivo": ESCENARIO["objetivo"],
            "criterios": ESCENARIO["criterios"],
            "fuente": "plan demo InTouch 15-09, frente A",
            "max_turns": 8,
        },
    )


def revertir_reclamo_contra_intouch(apps, schema_editor):
    EscenarioDePrueba = apps.get_model("simulator", "EscenarioDePrueba")
    EscenarioDePrueba.objects.filter(nombre=ESCENARIO["nombre"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("simulator", "0003_escenarios_intouch"),
    ]

    operations = [
        migrations.RunPython(poblar_reclamo_contra_intouch, revertir_reclamo_contra_intouch),
    ]
