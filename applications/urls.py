from django.urls import path
from . import views

urlpatterns = [
    # --- Main Job Views ---
    path('', views.job_list, name='job_list'),
    # FIX 2: Removed duplicate 'add/' route. Use 'create/' + name='job_create' consistently.
    # If any templates use {% url 'job_form' %}, update them to {% url 'job_create' %}.
    path('create/', views.job_create, name='job_create'),
    path('<int:pk>/', views.job_detail, name='job_detail'),

    # --- Tailoring & Retry ---
    path('retailor/<int:job_id>/', views.re_tailor_job, name='re_tailor_job'),

    # --- Download Endpoints ---
    path('download-tailored/<int:match_id>/', views.download_tailored_cv_pdf, name='download_tailored_cv'),
    # FIX 1: Was pointing to download_combined_pdf (wrong). Now correctly points to
    # generate_tailored_docx. Note: implement that view properly before using this URL.
    path('download-docx/<int:match_id>/', views.generate_tailored_docx, name='download_tailored_docx'),
    path('download-txt/<int:match_id>/', views.download_tailored_txt, name='download_txt'),
    path('download-cover-letter/<int:match_id>/', views.download_cover_letter_pdf, name='download_cover_letter'),
    path('download-combined/<int:match_id>/', views.download_combined_pdf, name='download_combined'),

    # --- Dashboard ---
    path('history/', views.application_history, name='application_history'),

    # --- Live Browser Automation ---
    path('live-apply/<int:match_id>/', views.live_apply_view, name='live_apply_view'),

    # --- API Endpoints ---
    path('api/status/<int:match_id>/', views.match_status_api, name='match_status'),
    path('api/logs/<int:match_id>/', views.match_logs_api, name='match_logs'),
    path('api/trigger-apply/<int:match_id>/', views.trigger_apply_api, name='trigger_apply'),
    path('api/quick-apply/<int:match_id>/', views.quick_apply_api, name='quick_apply_api'),
    path('api/send-email/<int:match_id>/', views.send_email_api, name='send_email_api'),

    # FIX 3 (note): <str:doc_type> accepts any value. Valid values are: 'cv', 'cover_letter', 'combined'.
    # Consider using a regex to restrict: re_path(r'^match/(?P<match_id>\d+)/pdf/(?P<doc_type>cv|cover_letter|combined)/$', ...)
    path('match/<int:match_id>/pdf/<str:doc_type>/', views.view_application_pdf, name='view_application_pdf'),
]