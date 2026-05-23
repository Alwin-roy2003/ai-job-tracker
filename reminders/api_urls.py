from django.urls import path
from . import api_views

app_name = "reminders_api"

urlpatterns = [
    path("", api_views.reminder_list_api, name="reminder-list"),
]
