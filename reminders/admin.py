from django.contrib import admin
from .models import Reminder


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("title", "application", "reminder_date", "is_completed")
    list_filter = ("is_completed", "reminder_date")
    search_fields = ("title",)
