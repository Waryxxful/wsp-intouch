from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from bot.whatsapp.client import WhatsAppCloudClient


@override_settings(WHATSAPP_TOKEN="test-token", WHATSAPP_PHONE_ID="123456")
class SendImageTest(SimpleTestCase):
    def setUp(self):
        self.client = WhatsAppCloudClient()

    @patch("bot.whatsapp.client._http.post")
    def test_manda_el_body_correcto_sin_caption(self, mock_post):
        mock_post.return_value.status_code = 200
        ok = self.client.send_image("56911112222", "https://qadash.in-touchcrm.cl/wsp/demo/media/model_images/koleos.jpg")
        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["type"], "image")
        self.assertEqual(
            kwargs["json"]["image"],
            {"link": "https://qadash.in-touchcrm.cl/wsp/demo/media/model_images/koleos.jpg"},
        )

    @patch("bot.whatsapp.client._http.post")
    def test_incluye_caption_si_se_pasa(self, mock_post):
        mock_post.return_value.status_code = 200
        self.client.send_image("56911112222", "https://x/img.jpg", caption="Koleos Techno")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["image"]["caption"], "Koleos Techno")

    @patch("bot.whatsapp.client._http.post")
    def test_status_distinto_de_200_devuelve_false(self, mock_post):
        mock_post.return_value.status_code = 400
        self.assertFalse(self.client.send_image("56911112222", "https://x/img.jpg"))


@override_settings(WHATSAPP_TOKEN="test-token", WHATSAPP_PHONE_ID="123456")
class SendDocumentTest(SimpleTestCase):
    def setUp(self):
        self.client = WhatsAppCloudClient()

    @patch("bot.whatsapp.client._http.post")
    def test_manda_el_body_correcto_sin_filename_ni_caption(self, mock_post):
        mock_post.return_value.status_code = 200
        ok = self.client.send_document("56911112222", "https://renault.cl/ficha.pdf")
        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["type"], "document")
        self.assertEqual(kwargs["json"]["document"], {"link": "https://renault.cl/ficha.pdf"})

    @patch("bot.whatsapp.client._http.post")
    def test_incluye_filename_y_caption_si_se_pasan(self, mock_post):
        mock_post.return_value.status_code = 200
        self.client.send_document(
            "56911112222", "https://renault.cl/ficha.pdf", filename="Ficha Koleos.pdf", caption="Ficha técnica",
        )
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["document"]["filename"], "Ficha Koleos.pdf")
        self.assertEqual(kwargs["json"]["document"]["caption"], "Ficha técnica")

    @patch("bot.whatsapp.client._http.post")
    def test_status_distinto_de_200_devuelve_false(self, mock_post):
        mock_post.return_value.status_code = 400
        self.assertFalse(self.client.send_document("56911112222", "https://renault.cl/ficha.pdf"))


@override_settings(WHATSAPP_TOKEN="test-token", WHATSAPP_PHONE_ID="123456")
class SendTemplateTest(SimpleTestCase):
    def setUp(self):
        self.client = WhatsAppCloudClient()

    @patch("bot.whatsapp.client._http.post")
    def test_manda_el_body_correcto_sin_components(self, mock_post):
        mock_post.return_value.status_code = 200
        ok = self.client.send_template("56911112222", "encuesta_servicio_tecnico")
        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["type"], "template")
        self.assertEqual(
            kwargs["json"]["template"], {"name": "encuesta_servicio_tecnico", "language": {"code": "es"}},
        )

    @patch("bot.whatsapp.client._http.post")
    def test_incluye_components_si_se_pasan(self, mock_post):
        mock_post.return_value.status_code = 200
        components = [{"type": "body", "parameters": [{"type": "text", "text": "Juan"}]}]
        self.client.send_template("56911112222", "encuesta_venta_auto_nuevo", components=components)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["template"]["components"], components)

    @patch("bot.whatsapp.client._http.post")
    def test_status_distinto_de_200_devuelve_false(self, mock_post):
        mock_post.return_value.status_code = 400
        self.assertFalse(self.client.send_template("56911112222", "x"))

    @patch("bot.whatsapp.client._http.post")
    def test_status_distinto_de_200_loguea_warning_con_el_body(self, mock_post):
        mock_post.return_value.status_code = 400
        mock_post.return_value.text = '{"error": {"message": "template not found"}}'
        with self.assertLogs("bot.whatsapp.client", level="WARNING") as captured:
            self.client.send_template("56911112222", "x")
        self.assertIn("template not found", captured.output[0])
