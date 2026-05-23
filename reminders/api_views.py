from django.http import JsonResponse
from .models import Reminder

def reminder_list_api(request):
    data = [
        {
            "id": r.id,
            "title": r.title,
            "due_date": r.due_date,
            "is_completed": r.is_completed,
        }
        for r in Reminder.objects.all()
    ]
    return JsonResponse(data, safe=False)
