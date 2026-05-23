from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# FIX 1: Import home from config/views.py.
# Make sure config/views.py exists with a 'home' view.
# If it doesn't exist, move the home view to an app (e.g. users/views.py)
# and update this import accordingly.
from .views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),

    # Django Auth URLs (login/logout/password reset)
    path("accounts/", include("django.contrib.auth.urls")),

    # Web Interface URLs
    path("applications/", include("applications.urls")),
    path("companies/", include("companies.urls")),
    path("reminders/", include("reminders.urls")),
    path("users/", include("users.urls")),

    # REST API URLs
    # FIX 3: Ensure all four api_urls.py files exist in their respective apps
    # before starting the server — a missing file will crash on startup.
    path("api/applications/", include("applications.api_urls")),
    path("api/companies/", include("companies.api_urls")),
    path("api/reminders/", include("reminders.api_urls")),
    path("api/users/", include("users.api_urls")),
]

# Serve media and static files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # FIX 2: Also serve static files in development (was missing before)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)