import jwt
from django.test import TestCase, RequestFactory
from django.conf import settings
from grancrm_auth.middleware import GranCRMAuthMiddleware


class GranCRMAuthMiddlewareTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = GranCRMAuthMiddleware(lambda request: request)

    def test_sin_cookie_jwt_payload_es_none(self):
        request = self.factory.get("/")
        result = self.middleware(request)
        self.assertIsNone(result.jwt_payload)

    def test_con_cookie_valida_decodifica_payload(self):
        token = jwt.encode({"email": "cliente@intouch.cl"}, settings.GRANCRM_JWT_SECRET, algorithm="HS256")
        request = self.factory.get("/")
        request.COOKIES["grancrm_session"] = token
        result = self.middleware(request)
        self.assertEqual(result.jwt_payload["email"], "cliente@intouch.cl")

    def test_con_cookie_invalida_payload_es_none(self):
        request = self.factory.get("/")
        request.COOKIES["grancrm_session"] = "esto-no-es-un-jwt"
        result = self.middleware(request)
        self.assertIsNone(result.jwt_payload)
