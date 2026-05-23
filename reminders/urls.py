from django.urls import path
from . import views

urlpatterns = [
    path("", views.reminder_list, name="reminder_list"),
    path("add/", views.reminder_create, name="reminder_create"),
    path("complete/<int:pk>/", views.reminder_complete, name="reminder_complete"),
    path("delete/<int:pk>/", views.reminder_delete, name="reminder_delete"),
]
