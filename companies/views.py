from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError
from .models import Company


def company_list(request):
    companies = Company.objects.all().order_by("name")
    return render(request, "companies/company_list.html", {"companies": companies})


@login_required  # FIX 3: Require login to create a company
def company_create(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()

        # FIX 1: Validate that name is not empty before trying to save
        if not name:
            messages.error(request, "❌ Platform name is required.")
            return render(request, "companies/company_form.html")

        try:
            Company.objects.create(
                name=name,
                platform_type=request.POST.get("platform_type", "board"),
                website=request.POST.get("website", ""),
                location=request.POST.get("location", ""),
                notes=request.POST.get("notes", ""),
            )
            # FIX 4: Show success message after creating
            messages.success(request, f"✅ Platform '{name}' added successfully!")
            return redirect("company_list")

        except IntegrityError:
            # FIX 1: Handle duplicate name gracefully instead of crashing
            messages.error(request, f"❌ A platform named '{name}' already exists.")
            return render(request, "companies/company_form.html")

    return render(request, "companies/company_form.html")


@login_required  # FIX 3: Require login to delete a company
def company_delete(request, pk):
    company = get_object_or_404(Company, pk=pk)

    # FIX 2: Only allow POST requests — prevents accidental deletion
    # by simply visiting the URL in a browser.
    if request.method == "POST":
        name = company.name
        company.delete()
        # FIX 4: Show success message after deleting
        messages.success(request, f"✅ Platform '{name}' deleted.")
        return redirect("company_list")

    # If GET request, show a confirmation page instead of deleting
    return render(request, "companies/company_confirm_delete.html", {"company": company})