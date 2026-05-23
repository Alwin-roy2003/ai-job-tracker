from django.db import models
from django.conf import settings

class Reminder(models.Model):
    # Use string reference 'applications.Job' instead of direct import
    application = models.ForeignKey(
        'applications.Job',  # <-- String reference, not direct import
        on_delete=models.CASCADE,
        related_name="reminders"
    )
    title = models.CharField(max_length=255)
    reminder_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title