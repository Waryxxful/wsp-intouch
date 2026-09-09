"""La compuerta que decide si la prosa del especialista se le puede mandar al cliente.

Contexto (docs/superpowers/specs/2026-09-03-canal-de-salida-prosa-natural-design.md):
la prosa pasa a SER la respuesta, y el modelo a veces filtra restos de nuestras
propias instrucciones al canal de texto. 6 de 8 fallos reales de Langfuse traian
un preambulo inventado; en 28 replays del estado exacto de produccion, 0 de 7 lo
traian. Depende del estado, asi que no se puede mandar el content crudo.
"""
from django.test import SimpleTestCase

from bot.flow.prosa import prosa_utilizable


class ProsaUtilizableTest(SimpleTestCase):
    def test_prosa_normal_pasa(self):
        self.assertTrue(prosa_utilizable(
            "La garantía de los usados es de 6 meses o 10.000 km. ¿Te muestro alguno?"))

    def test_prosa_con_preambulo_de_instruccion_no_pasa(self):
        # Los tres casos reales de Langfuse, textuales. Arrancan todos con un
        # espacio y restatean instrucciones nuestras.
        for texto in (
            " Usa la estructura de mensaje con parrafos separados por un salto de linea.\n\nEncontré varias opciones...",
            " Todos los demás campos son opcionales. `intent` y `stage` son obligatorios.",
            ' El primer caracter de tu respuesta SIEMPRE es "{"...',
        ):
            self.assertFalse(prosa_utilizable(texto), msg=texto[:50])

    def test_texto_vacio_o_solo_espacios_no_pasa(self):
        for texto in ("", "   ", "\n\n", None):
            self.assertFalse(prosa_utilizable(texto))

    def test_muy_corto_no_pasa(self):
        # "..." es el mensaje degenerado que el tool_choice forzado mandaba al
        # cliente (medido: 2/12 veces); una respuesta util nunca mide 3 caracteres.
        self.assertFalse(prosa_utilizable("..."))
        self.assertFalse(prosa_utilizable("ok"))

    def test_un_json_no_es_prosa(self):
        # Si el LLM devolvio el contrato viejo, lo maneja _parse_json_response,
        # no esta compuerta.
        self.assertFalse(prosa_utilizable('{"mensaje": "hola"}'))

    def test_una_respuesta_corta_pero_legitima_pasa(self):
        self.assertTrue(prosa_utilizable("Sí, aceptamos tu auto en parte de pago."))

    def test_un_json_malformado_tampoco_es_prosa(self):
        # Caso real que cazo un test de test_graph: el LLM devolvio el contrato
        # con comillas internas rotas. No parsea, pero mandarselo al cliente
        # crudo seria peor que reintentar.
        self.assertFalse(prosa_utilizable(
            '{"mensaje": "el arkana hybrid tiene precios "desde" varios", "handoff": false}'))

    def test_un_texto_que_empieza_con_llave_nunca_es_prosa(self):
        self.assertFalse(prosa_utilizable('{esto no es json ni prosa util}'))

    def test_un_json_en_bloque_de_codigo_tampoco_es_prosa(self):
        # _parse_json_response acepta ```json ... ```, asi que un contrato viejo
        # asi envuelto NO empieza con "{" y se habria colado como prosa,
        # mandandole al cliente el JSON con las comillas invertidas incluidas.
        self.assertFalse(prosa_utilizable('```json\n{"mensaje": "hola"}\n```'))
        self.assertFalse(prosa_utilizable('```\n{"mensaje": "hola"}\n```'))
