from django.http import HttpResponse

def user_list(request):
    return HttpResponse("Users page working")
