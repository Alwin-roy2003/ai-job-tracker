from django.urls import path
from . import views

urlpatterns = [
    path("", views.company_list, name="company_list"),
    path("add/", views.company_create, name="company_create"),
    path("delete/<int:pk>/", views.company_delete, name="company_delete"),
]
