from rest_framework import generics
from .models import User
from .serializers import UserSerializer

class UserListAPI(generics.ListAPIView):
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserSerializer


class UserDetailAPI(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
