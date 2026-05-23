import threading
# REMOVED: import io  ← was imported but never used
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.db.models import Q
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from .models import Job, AutomatedJobMatch, ApplicationLog
from companies.models import Company

# Import utility functions from utils.py
from .utils import (
    tailor_resume_logic,
    generate_tailored_pdf,
    generate_cover_letter_pdf,
    get_resume_text,
    extract_personal_info,
    send_application_email,
    normalize_text
)

# Browser automation import - FORCE ENABLE
try:
    from .utils import run_job_application_automation as simulate_job_application
    from .utils import SELENIUM_AVAILABLE as BROWSER_AUTOMATION_AVAILABLE
    print("[INFO] ✅ Browser automation loaded successfully!")
except ImportError as e:
    print(f"[WARNING] Browser automation import failed: {e}")
    BROWSER_AUTOMATION_AVAILABLE = False
    
    def simulate_job_application(match_id, quick_apply=False):
        """Dummy function when Selenium is not available"""
        print(f"[DUMMY] Would run automation for match {match_id}")
        return False

# ==========================================
# 1. MAIN DASHBOARD & JOB VIEWS
# ==========================================

@login_required
def job_list(request):
    """
    Main dashboard showing all jobs with PDF downloads
    """
    jobs = Job.objects.filter(user=request.user).order_by('-created_at')
    
    # Filter by Language
    lang = request.GET.get('language')
    if lang:
        jobs = jobs.filter(language=lang)
    
    # Search Filter
    query = request.GET.get('q')
    if query:
        jobs = jobs.filter(
            Q(title__icontains=query) | 
            Q(keywords__icontains=query) |
            Q(platform__icontains=query)
        )
    
    # Get recent applications (last 24 hours) with PDFs
    recent_applications = AutomatedJobMatch.objects.filter(
        job_query__user=request.user,
        applied_at__gte=timezone.now() - timedelta(hours=24)
    ).order_by('-applied_at')[:10]
    
    # Get all applied jobs with PDFs available
    applied_jobs = AutomatedJobMatch.objects.filter(
        job_query__user=request.user,
        status__in=['applied', 'completed']
    ).select_related('job_query').order_by('-applied_at')[:20]
    
    context = {
        'jobs': jobs,
        'recent_applications': recent_applications,
        'applied_jobs': applied_jobs,
    }
    
    return render(request, 'applications/job_list.html', context)


@login_required
def job_create(request):
    """Creates a new job search and triggers the AI tailoring process"""
    if request.method == "POST":
        platform_name = request.POST.get("company")
        
        new_job = Job.objects.create(
            user=request.user,
            title=request.POST.get("title"),
            platform=platform_name,
            keywords=request.POST.get("search_keywords", ""),
            language=request.POST.get("language", "english"),
            location=request.POST.get("location", ""),
            base_cv=request.FILES.get("resume"),
            status='applied'
        )
        
        # Run AI logic from utils.py
        success = tailor_resume_logic(new_job)
        
        if success:
            messages.success(request, "✅ Job created! AI is tailoring your resume...")
        else:
            messages.warning(request, "⚠️ Job created but AI processing failed.")
            
        return redirect("job_list")
    
    companies = Company.objects.all()
    return render(request, "applications/job_form.html", {"companies": companies})


@login_required
def job_detail(request, pk):
    """Detailed view for a specific job entry"""
    job = get_object_or_404(Job, pk=pk, user=request.user)
    matches = job.matches.all()
    return render(request, 'applications/job_detail.html', {
        'job': job,
        'matches': matches
    })


@login_required
def re_tailor_job(request, job_id):
    """Manual trigger to re-run AI tailoring for a job"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    if not job.base_cv:
        messages.error(request, "❌ No CV uploaded!")
        return redirect("job_list")
    
    success = tailor_resume_logic(job)
    
    if success:
        messages.success(request, "✨ AI successfully re-processed your materials!")
    else:
        messages.error(request, "❌ Processing failed.")
        
    return redirect("job_list")


# ==========================================
# 2. PDF DOWNLOAD VIEWS (SEPARATE CV & COVER LETTER)
# ==========================================

@login_required
def download_tailored_cv_pdf(request, match_id):
    """
    Download tailored CV as PDF.
    Customized per job description.
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    try:
        resume_text = ""
        if match.job_query.base_cv:
            resume_text = get_resume_text(match.job_query.base_cv.path)
        
        personal_info = extract_personal_info(resume_text)
        
        # Generate CV PDF only
        pdf_buffer = generate_tailored_pdf(match, personal_info, doc_type='cv')
        
        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="CV_{match.company_name}_{match.job_title}.pdf"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating CV PDF: {str(e)}")
        return redirect('job_list')


@login_required
def download_cover_letter_pdf(request, match_id):
    """
    Download Cover Letter as PDF.
    Customized per job description.
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    try:
        resume_text = ""
        if match.job_query.base_cv:
            resume_text = get_resume_text(match.job_query.base_cv.path)
        
        personal_info = extract_personal_info(resume_text)
        
        # Generate Cover Letter PDF only
        pdf_buffer = generate_cover_letter_pdf(match, personal_info)
        
        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="CoverLetter_{match.company_name}_{match.job_title}.pdf"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating Cover Letter PDF: {str(e)}")
        return redirect('job_list')


@login_required
def download_combined_pdf(request, match_id):
    """
    Download CV and Cover Letter combined in one PDF.
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    try:
        resume_text = ""
        if match.job_query.base_cv:
            resume_text = get_resume_text(match.job_query.base_cv.path)
        
        personal_info = extract_personal_info(resume_text)
        
        # Generate combined PDF
        pdf_buffer = generate_tailored_pdf(match, personal_info, doc_type='combined')
        
        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Application_{match.company_name}_{match.job_title}.pdf"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error generating PDF: {str(e)}")
        return redirect('job_list')


@login_required
def download_tailored_txt(request, match_id):
    """Download combined CV and Cover Letter as a .txt file"""
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    content = f"APPLICATION FOR {match.company_name.upper()}\n"
    content += "="*50 + "\n"
    content += f"Role: {match.job_title}\n"
    content += f"ATS Score: {match.ats_score}%\n\n"
    content += "--- TAILORED RESUME ---\n"
    content += match.tailored_cv_text or "CV not generated."
    content += "\n\n--- COVER LETTER ---\n"
    content += match.cover_letter_text or "No cover letter generated."

    response = HttpResponse(content, content_type='text/plain')
    response['Content-Disposition'] = f'attachment; filename="Application_{match.company_name}.txt"'
    return response


# ==========================================
# 3. LIVE AUTOMATION VIEW
# ==========================================

@login_required
def live_apply_view(request, match_id):
    """Live automation view"""
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    logs = ApplicationLog.objects.filter(match=match).order_by('-timestamp')[:50]
    
    return render(request, 'applications/live_apply.html', {
        'match': match,
        'logs': logs,
        'selenium_available': BROWSER_AUTOMATION_AVAILABLE
    })


# ==========================================
# 4. API ENDPOINTS (FOR DASHBOARD & LIVE TERMINAL)
# ==========================================

@login_required
def match_status_api(request, match_id):
    """API for the dashboard to check if automation finished"""
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    return JsonResponse({
        'success': True,
        'status': match.status,
        'company': match.company_name,
        'job_title': match.job_title,
        'ats_score': match.ats_score
    })


@login_required
@require_http_methods(["POST"])
def send_email_api(request, match_id):
    """
    Manually triggers the result email with both PDF attachments.
    Sends CV and Cover Letter separately.
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    try:
        resume_text = get_resume_text(match.job_query.base_cv.path) if match.job_query.base_cv else ""
        personal_info = extract_personal_info(resume_text)
        
        if send_application_email(match, personal_info):
            return JsonResponse({'success': True, 'message': 'Email sent successfully!'})
        return JsonResponse({'success': False, 'error': 'Email sending failed. Check SMTP settings.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def match_logs_api(request, match_id):
    """Fetches real-time browser logs for the Live Apply terminal window"""
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    try:
        logs = match.logs.all().order_by('timestamp')
        logs_data = [{
            'action': log.action,
            'description': log.description,
            'time': log.timestamp.strftime('%H:%M:%S'),
        } for log in logs]
    except Exception:
        logs_data = []
    
    return JsonResponse({
        'success': True,
        'logs': logs_data,
        'match_status': match.status
    })


@login_required
@require_http_methods(["POST"])
def trigger_apply_api(request, match_id):
    """
    API called by 'One-Click' automation script. 
    Launches the Selenium browser in a background thread.
    """
    if not BROWSER_AUTOMATION_AVAILABLE:
        return JsonResponse({'success': False, 'error': 'Selenium not detected.'}, status=503)
    
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    # Prevent starting multiple sessions for one job
    if match.status == 'applying':
        return JsonResponse({'success': True, 'message': 'Already running'})

    match.status = 'applying'
    match.save()
    
    # Start the automated browser session (One-Click)
    thread = threading.Thread(
        target=simulate_job_application,
        args=(match.id,) 
    )
    thread.daemon = True
    thread.start()
    
    return JsonResponse({
        'success': True, 
        'status': 'started',
        'message': 'Automation session launched.'
    })


# ==========================================
# 5. DASHBOARD & HISTORY VIEWS
# ==========================================

@login_required
def application_history(request):
    """
    View all applied jobs with PDF download links.
    Shows application status and dates.
    """
    applications = AutomatedJobMatch.objects.filter(
        job_query__user=request.user
    ).select_related('job_query').order_by('-applied_at')
    
    return render(request, 'applications/application_history.html', {
        'applications': applications
    })


@login_required
def view_application_pdf(request, match_id, doc_type):
    """
    View PDF inline in browser (not download).
    doc_type: 'cv', 'cover_letter', or 'combined'
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    try:
        resume_text = ""
        if match.job_query.base_cv:
            resume_text = get_resume_text(match.job_query.base_cv.path)
        
        personal_info = extract_personal_info(resume_text)
        
        if doc_type == 'cv':
            pdf_buffer = generate_tailored_pdf(match, personal_info, doc_type='cv')
            filename = f"CV_{match.company_name}.pdf"
        elif doc_type == 'cover_letter':
            pdf_buffer = generate_cover_letter_pdf(match, personal_info)
            filename = f"CoverLetter_{match.company_name}.pdf"
        else:
            pdf_buffer = generate_tailored_pdf(match, personal_info, doc_type='combined')
            filename = f"Application_{match.company_name}.pdf"
        
        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response
        
    except Exception as e:
        messages.error(request, f"Error viewing PDF: {str(e)}")
        return redirect('job_list')


@login_required
@require_http_methods(["POST"])
def quick_apply_api(request, match_id):
    """
    Quick apply: Just click apply button (for jobs posted < 24 hours ago).
    Skips complex form filling, just clicks the apply button.
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    # FIX 1: Use applied_at instead of created_at (matches the model field used elsewhere)
    if match.applied_at < timezone.now() - timedelta(hours=24):
        return JsonResponse({
            'success': False, 
            'error': 'Job is older than 24 hours. Use full automation instead.'
        })
    
    if not BROWSER_AUTOMATION_AVAILABLE:
        return JsonResponse({'success': False, 'error': 'Browser automation not available.'}, status=503)

    # FIX 2: Set status BEFORE starting thread (prevents race condition)
    match.status = 'applying'
    match.save()

    # Start quick apply (just click apply button)
    thread = threading.Thread(
        target=simulate_job_application,
        args=(match.id,),
        kwargs={'quick_apply': True}
    )
    thread.daemon = True
    thread.start()
    
    return JsonResponse({
        'success': True,
        'message': 'Quick apply started (clicking apply button only).'
    })


# ==========================================
# 6. ADDITIONAL DOWNLOAD VIEW
# ==========================================

@login_required
def generate_tailored_docx(request, match_id):
    """
    Generate tailored DOCX file.
    TODO: Implement actual DOCX generation using python-docx.
    Install with: pip install python-docx
    """
    match = get_object_or_404(AutomatedJobMatch, id=match_id, job_query__user=request.user)
    
    # NOTE: This is a placeholder. To implement properly:
    # from docx import Document
    # import io
    # doc = Document()
    # doc.add_heading(match.job_title, 0)
    # doc.add_paragraph(match.tailored_cv_text or "")
    # buffer = io.BytesIO()
    # doc.save(buffer)
    # buffer.seek(0)
    # response = HttpResponse(buffer.read(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    # response['Content-Disposition'] = f'attachment; filename="CV_{match.company_name}.docx"'
    # return response

    messages.warning(request, "⚠️ DOCX generation is not yet implemented.")
    return redirect('job_list')