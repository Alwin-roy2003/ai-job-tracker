from django.urls import path
from . import api_views

app_name = "companies_api"

urlpatterns = [
    path("", api_views.company_list_api, name="company-list"),
]
