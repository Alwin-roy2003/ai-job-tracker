from django.shortcuts import render, redirect, get_object_or_404
from .models import Reminder
from applications.models import Job


def reminder_list(request):
    reminders = Reminder.objects.select_related("application").all()
    return render(request, "reminders/reminder_list.html", {
        "reminders": reminders
    })


def reminder_create(request):
    if request.method == "POST":
        title = request.POST.get("title")
        application_id = request.POST.get("application")
        reminder_date = request.POST.get("reminder_date")

        application = get_object_or_404(Job, id=application_id)

        Reminder.objects.create(
            title=title,
            application=application,
            reminder_date=reminder_date or None
        )
        return redirect("reminder_list")

    applications = Job.objects.all()
    return render(request, "reminders/reminder_form.html", {
        "applications": applications
    })


def reminder_complete(request, pk):
    reminder = get_object_or_404(Reminder, pk=pk)
    reminder.is_completed = True
    reminder.save()
    return redirect("reminder_list")


def reminder_delete(request, pk):
    reminder = get_object_or_404(Reminder, pk=pk)
    reminder.delete()
    return redirect("reminder_list")
