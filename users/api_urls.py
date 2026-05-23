from django.urls import path
from .api_views import UserListAPI, UserDetailAPI

urlpatterns = [
    path("", UserListAPI.as_view(), name="api_user_list"),
    path("<int:pk>/", UserDetailAPI.as_view(), name="api_user_detail"),
]
