import os
import tempfile

from django.test import TestCase, override_settings


class MediaUrlServeTest(TestCase):
    def test_sirve_un_archivo_dentro_de_media_root_con_content_type_jpeg(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "koleos.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0fake jpeg bytes")
        with override_settings(MEDIA_ROOT=tmp):
            resp = self.client.get("/demo/media/koleos.jpg")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/jpeg")

    def test_archivo_inexistente_devuelve_404(self):
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            resp = self.client.get("/demo/media/no-existe.jpg")
        self.assertEqual(resp.status_code, 404)


class HealthzTest(TestCase):
    # Dockerfile HEALTHCHECK pega esta ruta desde dentro del propio
    # contenedor (ver docs/PENDIENTES.md, "Infraestructura -- dependencias y
    # despliegue") -- sin auth a proposito, y con un SELECT 1 real para que
    # una BD caida tambien marque el contenedor unhealthy, no solo un
    # gunicorn colgado.
    def test_devuelve_200_con_bd_arriba(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
