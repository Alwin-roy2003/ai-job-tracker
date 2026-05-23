from django.urls import path
from . import api_views

urlpatterns = [
    path('jobs/', api_views.JobListCreateAPIView.as_view(), name='job-list'),
    path('jobs/<int:pk>/', api_views.JobDetailAPIView.as_view(), name='job-detail'),
    path('jobs/<int:job_id>/matches/', api_views.JobMatchListAPIView.as_view(), name='job-matches'),
    path('jobs/<int:job_id>/matches/<int:match_id>/generate/', api_views.GenerateTailoredContentAPIView.as_view(), name='generate-content'),
]