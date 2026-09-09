import jwt
from django.conf import settings


class GranCRMAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request.COOKIES.get("grancrm_session")
        request.jwt_payload = None

        if token:
            try:
                request.jwt_payload = jwt.decode(
                    token,
                    settings.GRANCRM_JWT_SECRET,
                    algorithms=["HS256"],
                )
            except jwt.InvalidTokenError:
                pass

        return self.get_response(request)
