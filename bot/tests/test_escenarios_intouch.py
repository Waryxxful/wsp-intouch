"""Los escenarios cubren la tabla de criterios de aceptacion del spec §11.4.

No prueban el comportamiento del LLM -- eso lo hace el simulador contra el
modelo real -- sino que los escenarios EXISTAN y cubran cada caso. Un criterio
de aceptacion sin escenario es un criterio que nadie va a verificar.

Nota sobre los nombres de campo: el brief de esta tarea (task-17-brief.md)
asumia `slug`, `descripcion` y `criterio_de_exito` en EscenarioDePrueba. El
modelo real (bot/simulator/models.py) no los tiene -- usa `nombre` (CharField
unique, que ya cumple de slug en la migracion 0002 existente) y `criterios`
(JSONField, lista de strings) en vez de un `criterio_de_exito` unico. No hay
campo `descripcion` separado: para el chequeo de vocabulario automotriz
heredado se usa `persona`, el unico campo que no necesita mencionar el rubro
del contacto (el escenario "automotriz-sin-subtipo" SI necesita decir
"automotriz" en su objetivo/criterio, porque ese es justamente el caso que
prueba -- un contacto CUYA PROPIA empresa es del sector automotriz, no un
vehiculo que vende InTouch).
"""
from django.test import TestCase

from bot.simulator.models import EscenarioDePrueba

CASOS = {
    "solo-saluda", "datos-completos", "pide-contacto-sin-correo",
    "se-despide-sin-datos", "no-registrar-mis-datos", "automotriz-sin-subtipo",
    "contact-center-mixto", "sigue-tras-registrar", "precio-inventado",
    "pide-instrucciones-internas", "consulta-de-soporte",
    "no-afirma-registro", "capacidad-que-no-existe",
    "reclamo-contra-intouch",
}


class CoberturaTest(TestCase):
    def test_esta_un_escenario_por_criterio_de_aceptacion(self):
        self.assertEqual(set(EscenarioDePrueba.objects.values_list("nombre", flat=True)),
                         CASOS)

    def test_no_quedan_escenarios_de_otro_cliente(self):
        crudo = " ".join(EscenarioDePrueba.objects.values_list("persona", flat=True))
        for palabra in ("auto", "taller", "vehículo", "patente"):
            self.assertNotIn(palabra.lower(), crudo.lower(), palabra)

    def test_cada_escenario_declara_que_espera(self):
        for escenario in EscenarioDePrueba.objects.all():
            self.assertTrue(escenario.criterios, escenario.nombre)
            self.assertTrue(escenario.criterios[0].strip(), escenario.nombre)
