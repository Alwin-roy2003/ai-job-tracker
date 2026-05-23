from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Q  # This allows us to search multiple fields
from applications.models import Job

@login_required
def home(request):
    """
    Main dashboard view. 
    Handles the Job List, Category filtering, and Keyword searching.
    """
    # 1. Start with all jobs belonging to you
    jobs = Job.objects.filter(user=request.user).order_by('-applied_date')

    # 2. Logic for the Category Buttons (English / German)
    # Triggered by clicking buttons like <a href="?language=german">
    language_filter = request.GET.get('language')
    if language_filter:
        jobs = jobs.filter(language=language_filter)

    # 3. Logic for the Search Box (Keywords)
    # Looks into Job Title, Platform Name, and your custom Search Keywords
    query = request.GET.get('q')
    if query:
        jobs = jobs.filter(
            Q(title__icontains=query) | 
            Q(company__name__icontains=query) | 
            Q(search_keywords__icontains=query)
        )

    context = {
        'jobs': jobs,
    }
    
    # We display the job_list.html as your main dashboard
    return render(request, 'applications/job_list.html', context)