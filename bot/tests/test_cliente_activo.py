from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.checks import run_checks
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from bot.models import VehiculoCatalogo, Sucursal, Servicio, ScrapingSource, PromptVersion, CustomSpecialist, get_active_prompt, save_prompt_version, deactivate_prompt


class VehiculoCatalogoClienteActivoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        # .create() en un manager filtrado igual inserta la fila -- el filtro de
        # get_queryset() solo aplica a lecturas, no a escrituras.
        VehiculoCatalogo.objects.create(modelo="Koleos", cliente="renault")
        VehiculoCatalogo.objects.create(modelo="Compass", cliente="astara")

        with override_settings(CLIENTE_ACTIVO="renault"):
            self.assertEqual(
                list(VehiculoCatalogo.objects.values_list("modelo", flat=True)), ["Koleos"]
            )
        with override_settings(CLIENTE_ACTIVO="astara"):
            self.assertEqual(
                list(VehiculoCatalogo.objects.values_list("modelo", flat=True)), ["Compass"]
            )

    def test_todos_los_clientes_no_filtra(self):
        VehiculoCatalogo.objects.create(modelo="Koleos", cliente="renault")
        VehiculoCatalogo.objects.create(modelo="Compass", cliente="astara")
        self.assertEqual(VehiculoCatalogo.todos_los_clientes.count(), 2)

    def test_cliente_default_es_renault(self):
        vehiculo = VehiculoCatalogo.objects.create(modelo="Koleos")
        self.assertEqual(vehiculo.cliente, "renault")

    @override_settings(DEBUG=True)
    def test_admin_changelist_solo_lista_cliente_activo(self):
        VehiculoCatalogo.objects.create(modelo="Koleos", cliente="renault")
        VehiculoCatalogo.objects.create(modelo="Compass", cliente="astara")
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="renault"):
            resp = self.client.get("/django-admin/bot/vehiculocatalogo/")
        self.assertContains(resp, "Koleos")
        self.assertNotContains(resp, "Compass")

    @override_settings(DEBUG=True)
    def test_admin_alta_estampa_cliente_activo_no_el_del_select(self):
        # Fix 4: el formulario de alta del admin de Django guardaba cliente en
        # lo que el operador dejara en el select (o el default del campo,
        # "renault", si no lo tocaba) -- sin relacion con CLIENTE_ACTIVO real
        # del proceso. Se manda "renault" a proposito en el POST para probar
        # que el mixin lo pisa igual con el cliente activo real.
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.post("/django-admin/bot/vehiculocatalogo/add/", data={
                "modelo": "Compass", "version": "", "specs": "{}", "url_fuente": "",
                "cliente": "renault",
            })
        self.assertEqual(resp.status_code, 302, resp.context["adminform"].form.errors if resp.status_code == 200 else "")
        vehiculo = VehiculoCatalogo.todos_los_clientes.get(modelo="Compass")
        self.assertEqual(vehiculo.cliente, "astara")


class SucursalClienteActivoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        Sucursal.objects.create(nombre="Vitacura", cliente="astara")
        Sucursal.objects.create(nombre="Providencia", cliente="renault")

        with override_settings(CLIENTE_ACTIVO="astara"):
            self.assertEqual(
                list(Sucursal.objects.values_list("nombre", flat=True)), ["Vitacura"]
            )

    def test_todos_los_clientes_no_filtra(self):
        Sucursal.objects.create(nombre="Vitacura", cliente="astara")
        Sucursal.objects.create(nombre="Providencia", cliente="renault")
        self.assertEqual(Sucursal.todos_los_clientes.count(), 2)

    @override_settings(DEBUG=True)
    def test_admin_changelist_solo_lista_cliente_activo(self):
        Sucursal.objects.create(nombre="Vitacura", cliente="astara")
        Sucursal.objects.create(nombre="Providencia", cliente="renault")
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.get("/django-admin/bot/sucursal/")
        self.assertContains(resp, "Vitacura")
        self.assertNotContains(resp, "Providencia")

    @override_settings(DEBUG=True)
    def test_admin_alta_estampa_cliente_activo_no_el_del_select(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.post("/django-admin/bot/sucursal/add/", data={
                # direccion vacia a proposito -- el signal post_save de
                # geocodificacion no hace nada sin direccion (ver PENDIENTES.md).
                "nombre": "Movicenter", "direccion": "", "horario_texto": "9:00-18:00",
                "categorias": "[]", "cliente": "renault",
            })
        self.assertEqual(resp.status_code, 302, resp.context["adminform"].form.errors if resp.status_code == 200 else "")
        sucursal = Sucursal.todos_los_clientes.get(nombre="Movicenter")
        self.assertEqual(sucursal.cliente, "astara")


class ServicioClienteActivoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        Servicio.objects.create(nombre="Mantención 10.000km", cliente="renault")
        Servicio.objects.create(nombre="Cambio de aceite", cliente="astara")

        with override_settings(CLIENTE_ACTIVO="renault"):
            self.assertEqual(
                list(Servicio.objects.values_list("nombre", flat=True)), ["Mantención 10.000km"]
            )

    def test_todos_los_clientes_no_filtra(self):
        Servicio.objects.create(nombre="Mantención 10.000km", cliente="renault")
        Servicio.objects.create(nombre="Cambio de aceite", cliente="astara")
        self.assertEqual(Servicio.todos_los_clientes.count(), 2)

    @override_settings(DEBUG=True)
    def test_admin_changelist_solo_lista_cliente_activo(self):
        Servicio.objects.create(nombre="Mantención 10.000km", cliente="renault")
        Servicio.objects.create(nombre="Cambio de aceite", cliente="astara")
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="renault"):
            resp = self.client.get("/django-admin/bot/servicio/")
        self.assertContains(resp, "Mantenci")
        self.assertNotContains(resp, "Cambio de aceite")

    @override_settings(DEBUG=True)
    def test_admin_alta_estampa_cliente_activo_no_el_del_select(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.post("/django-admin/bot/servicio/add/", data={
                "nombre": "Cambio de aceite", "duracion_min": "30", "cliente": "renault",
            })
        self.assertEqual(resp.status_code, 302, resp.context["adminform"].form.errors if resp.status_code == 200 else "")
        servicio = Servicio.todos_los_clientes.get(nombre="Cambio de aceite")
        self.assertEqual(servicio.cliente, "astara")


class ScrapingSourceClienteActivoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        ScrapingSource.objects.create(url="https://renault.cl", cliente="renault")
        ScrapingSource.objects.create(url="https://astararetail.cl", cliente="astara")

        with override_settings(CLIENTE_ACTIVO="astara"):
            self.assertEqual(
                list(ScrapingSource.objects.values_list("url", flat=True)),
                ["https://astararetail.cl"],
            )

    def test_todos_los_clientes_no_filtra(self):
        ScrapingSource.objects.create(url="https://renault.cl", cliente="renault")
        ScrapingSource.objects.create(url="https://astararetail.cl", cliente="astara")
        self.assertEqual(ScrapingSource.todos_los_clientes.count(), 2)

    @override_settings(DEBUG=True)
    def test_admin_changelist_solo_lista_cliente_activo(self):
        ScrapingSource.objects.create(url="https://renault.cl", cliente="renault")
        ScrapingSource.objects.create(url="https://astararetail.cl", cliente="astara")
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.get("/django-admin/bot/scrapingsource/")
        self.assertContains(resp, "astararetail.cl")
        self.assertNotContains(resp, "renault.cl")

    @override_settings(DEBUG=True)
    def test_admin_alta_estampa_cliente_activo_no_el_del_select(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

        with override_settings(CLIENTE_ACTIVO="astara"):
            resp = self.client.post("/django-admin/bot/scrapingsource/add/", data={
                "url": "https://astararetail.cl/nueva", "nombre": "", "frecuencia_horas": "0",
                "cliente": "renault",
            })
        self.assertEqual(resp.status_code, 302, resp.context["adminform"].form.errors if resp.status_code == 200 else "")
        fuente = ScrapingSource.todos_los_clientes.get(url="https://astararetail.cl/nueva")
        self.assertEqual(fuente.cliente, "astara")


class PromptVersionClienteActivoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        PromptVersion.objects.create(agente="global", prompt="prompt renault", cliente="renault")
        PromptVersion.objects.create(agente="global", prompt="prompt astara", cliente="astara")

        with override_settings(CLIENTE_ACTIVO="astara"):
            self.assertEqual(
                list(PromptVersion.objects.values_list("prompt", flat=True)), ["prompt astara"]
            )

    def test_todos_los_clientes_no_filtra(self):
        PromptVersion.objects.create(agente="global", prompt="prompt renault", cliente="renault")
        PromptVersion.objects.create(agente="global", prompt="prompt astara", cliente="astara")
        self.assertEqual(PromptVersion.todos_los_clientes.count(), 2)


class CustomSpecialistClienteActivoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        CustomSpecialist.objects.create(
            slug="ventas-renault", label="Ventas", descripcion="Ventas Renault", cliente="renault"
        )
        CustomSpecialist.objects.create(
            slug="ventas-astara", label="Ventas", descripcion="Ventas Astara", cliente="astara"
        )

        with override_settings(CLIENTE_ACTIVO="renault"):
            self.assertEqual(
                list(CustomSpecialist.objects.values_list("slug", flat=True)), ["ventas-renault"]
            )

    def test_todos_los_clientes_no_filtra(self):
        CustomSpecialist.objects.create(
            slug="ventas-renault", label="Ventas", descripcion="Ventas Renault", cliente="renault"
        )
        CustomSpecialist.objects.create(
            slug="ventas-astara", label="Ventas", descripcion="Ventas Astara", cliente="astara"
        )
        self.assertEqual(CustomSpecialist.todos_los_clientes.count(), 2)


class PromptVersionHelpersClienteActivoTest(TestCase):
    def test_save_prompt_version_estampa_el_cliente_activo(self):
        with override_settings(CLIENTE_ACTIVO="astara"):
            version = save_prompt_version("global", "prompt astara")
        self.assertEqual(version.cliente, "astara")

    def test_save_prompt_version_no_desactiva_la_version_del_otro_cliente(self):
        with override_settings(CLIENTE_ACTIVO="renault"):
            save_prompt_version("global", "prompt renault")
        with override_settings(CLIENTE_ACTIVO="astara"):
            save_prompt_version("global", "prompt astara")

        renault_version = PromptVersion.todos_los_clientes.get(cliente="renault", agente="global")
        astara_version = PromptVersion.todos_los_clientes.get(cliente="astara", agente="global")
        self.assertTrue(renault_version.activa)
        self.assertTrue(astara_version.activa)

    def test_get_active_prompt_no_ve_el_prompt_del_otro_cliente(self):
        with override_settings(CLIENTE_ACTIVO="renault"):
            save_prompt_version("global", "prompt renault")
        with override_settings(CLIENTE_ACTIVO="astara"):
            save_prompt_version("global", "prompt astara")
            self.assertEqual(get_active_prompt("global"), "prompt astara")
        with override_settings(CLIENTE_ACTIVO="renault"):
            self.assertEqual(get_active_prompt("global"), "prompt renault")

    def test_deactivate_prompt_no_afecta_al_otro_cliente(self):
        with override_settings(CLIENTE_ACTIVO="renault"):
            save_prompt_version("global", "prompt renault")
        with override_settings(CLIENTE_ACTIVO="astara"):
            save_prompt_version("global", "prompt astara")
            deactivate_prompt("global")
            self.assertEqual(get_active_prompt("global"), "")
        with override_settings(CLIENTE_ACTIVO="renault"):
            self.assertEqual(get_active_prompt("global"), "prompt renault")


class ClienteActivoSystemCheckTest(TestCase):
    """Fix 1: CLIENTE_ACTIVO invalido debe fallar ruidosamente al arrancar
    (via django.core.checks), no dejar los 6 managers filtrados devolviendo
    querysets vacios en silencio."""

    def test_valor_invalido_produce_error_de_check(self):
        for valor_invalido in ("Astara", "astara ", "astera", ""):
            with self.subTest(valor_invalido=valor_invalido):
                with override_settings(CLIENTE_ACTIVO=valor_invalido):
                    errors = run_checks()
                ids = [e.id for e in errors]
                self.assertIn("bot.E001", ids, f"esperaba error de check para CLIENTE_ACTIVO={valor_invalido!r}")

    def test_valores_validos_no_producen_error(self):
        for valor_valido in ("renault", "astara"):
            with self.subTest(valor_valido=valor_valido):
                with override_settings(CLIENTE_ACTIVO=valor_valido):
                    errors = run_checks()
                ids = [e.id for e in errors]
                self.assertNotIn("bot.E001", ids)


class RagSchemaClienteActivoCheckTest(TestCase):
    """Fix 2: RAG_SCHEMA (env var que resuelve el schema de Supabase del
    retrieval en vivo, bot/rag/cliente.py) no debe poder quedar desincronizada
    de CLIENTE_ACTIVO al arrancar -- sin esto, flippear un cliente para
    probarlo (o revertirlo) y olvidar la otra variable deja al bot buscando
    conocimiento del cliente equivocado en silencio."""

    @patch.dict("os.environ", {"RAG_SCHEMA": "astara"})
    def test_rag_schema_distinto_de_cliente_activo_produce_error(self):
        with override_settings(CLIENTE_ACTIVO="renault"):
            errors = run_checks()
        ids = [e.id for e in errors]
        self.assertIn("bot.E002", ids)

    def test_rag_schema_igual_a_cliente_activo_no_produce_error(self):
        for cliente in ("renault", "astara"):
            with self.subTest(cliente=cliente):
                with patch.dict("os.environ", {"RAG_SCHEMA": cliente}):
                    with override_settings(CLIENTE_ACTIVO=cliente):
                        errors = run_checks()
                ids = [e.id for e in errors]
                self.assertNotIn("bot.E002", ids)

    def test_rag_schema_sin_setear_no_produce_error(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("RAG_SCHEMA", None)
            with override_settings(CLIENTE_ACTIVO="astara"):
                errors = run_checks()
        ids = [e.id for e in errors]
        self.assertNotIn("bot.E002", ids)


class ScrapingSourceUnicidadPorClienteTest(TestCase):
    """Fix 3: url ya no es unique=True global -- unica por (cliente, url)."""

    def test_misma_url_en_clientes_distintos_no_choca(self):
        ScrapingSource.todos_los_clientes.create(url="https://x.cl", cliente="renault")
        ScrapingSource.todos_los_clientes.create(url="https://x.cl", cliente="astara")
        self.assertEqual(ScrapingSource.todos_los_clientes.filter(url="https://x.cl").count(), 2)

    def test_misma_url_en_mismo_cliente_lanza_integrity_error(self):
        ScrapingSource.todos_los_clientes.create(url="https://x.cl", cliente="renault")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ScrapingSource.todos_los_clientes.create(url="https://x.cl", cliente="renault")


class CustomSpecialistUnicidadPorClienteTest(TestCase):
    """Fix 3: slug ya no es unique=True global -- unico por (cliente, slug)."""

    def test_mismo_slug_en_clientes_distintos_no_choca(self):
        CustomSpecialist.todos_los_clientes.create(
            slug="ventas", label="Ventas", descripcion="Ventas", cliente="renault"
        )
        CustomSpecialist.todos_los_clientes.create(
            slug="ventas", label="Ventas", descripcion="Ventas", cliente="astara"
        )
        self.assertEqual(CustomSpecialist.todos_los_clientes.filter(slug="ventas").count(), 2)

    def test_mismo_slug_en_mismo_cliente_lanza_integrity_error(self):
        CustomSpecialist.todos_los_clientes.create(
            slug="ventas", label="Ventas", descripcion="Ventas", cliente="renault"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomSpecialist.todos_los_clientes.create(
                    slug="ventas", label="Ventas", descripcion="Ventas", cliente="renault"
                )
