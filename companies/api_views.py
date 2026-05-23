from django.http import JsonResponse
from .models import Company

def company_list_api(request):
    data = [
        {
            "id": company.id,
            "name": company.name,
            "website": company.website,
            "created_at": company.created_at,
        }
        for company in Company.objects.all()
    ]
    return JsonResponse(data, safe=False)
