from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import RoleTokenObtainPairSerializer


class LoginView(TokenObtainPairView):
    serializer_class = RoleTokenObtainPairSerializer
