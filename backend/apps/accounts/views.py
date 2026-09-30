from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView


class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class MeView(APIView):
    """Who am I? The app uses this to decide which tabs to show (e.g. approvals)."""

    def get(self, request):
        staff = request.user.staff
        return Response(
            {
                "id": request.user.id,
                "username": request.user.get_username(),
                "name": request.user.get_full_name(),
                "role": staff.role,
                "centre": {
                    "id": staff.centre_id,
                    "name": staff.centre.name,
                    "code": staff.centre.code,
                },
            }
        )
