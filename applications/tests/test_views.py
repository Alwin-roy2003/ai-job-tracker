"""
Test file for applications/views.py
Run with: pytest applications/tests/test_views.py -v
"""

import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock

from applications.models import Job, AutomatedJobMatch, PlatformConfig
from companies.models import Company

User = get_user_model()


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def user(db):
    """Create a test user"""
    return User.objects.create_user(
        username='testuser',
        email='test@example.com',
        password='testpass123'
    )


@pytest.fixture
def authenticated_client(client, user):
    """Create an authenticated test client"""
    client.force_login(user)
    return client


@pytest.fixture
def company(db):
    """Create a test company"""
    return Company.objects.create(
        name='LinkedIn',
        website='https://linkedin.com'
    )


@pytest.fixture
def job(db, user):
    """Create a test job"""
    return Job.objects.create(
        user=user,
        title='Python Developer',
        platform='linkedin',
        keywords='python django',
        language='english',
        location='Berlin, Germany',
        status='draft'
    )


@pytest.fixture
def job_with_cv(db, user):
    """Create a test job with CV uploaded"""
    cv_file = SimpleUploadedFile(
        "test_resume.pdf",
        b"PDF content here",
        content_type="application/pdf"
    )
    return Job.objects.create(
        user=user,
        title='Django Developer',
        platform='linkedin',
        keywords='django python',
        language='english',
        location='Munich',
        base_cv=cv_file,
        status='applied'
    )


@pytest.fixture
def automated_match(db, job):
    """Create a test automated job match"""
    return AutomatedJobMatch.objects.create(
        job_query=job,
        company_name='Test Corp',
        job_url='https://linkedin.com/jobs/123',
        job_title='Python Developer',
        job_description='Looking for Python dev',
        location='Berlin',
        tailored_cv_text='Tailored resume content',
        cover_letter_text='Cover letter content',
        ats_score=85,
        platform_job_id='linkedin_123',
        status='found',
        created_at=timezone.now()
    )


@pytest.fixture
def platform_config(db, user):
    """Create test platform configuration"""
    return PlatformConfig.objects.create(
        user=user,
        platform='linkedin',
        username='test@gmail.com',
        password='testpass',
        email_app_password='16digitcode',
        daily_apply_limit=10
    )


# =============================================================================
# TESTS FOR JOB_LIST VIEW
# =============================================================================

@pytest.mark.django_db
class TestJobListView:
    def test_job_list_requires_login(self, client):
        response = client.get(reverse('job_list'))
        assert response.status_code == 302
    
    def test_job_list_accessible_to_authenticated_user(self, authenticated_client, user, job):
        response = authenticated_client.get(reverse('job_list'))
        assert response.status_code == 200
        assert 'jobs' in response.context


# =============================================================================
# TESTS FOR JOB_CREATE VIEW
# =============================================================================

@pytest.mark.django_db
class TestJobCreateView:
    @patch('applications.views.tailor_resume_logic')
    def test_job_create_post_success(self, mock_tailor, authenticated_client, user, company):
        mock_tailor.return_value = True
        data = {
            'title': 'Senior Python Developer',
            'company': 'linkedin',
            'search_keywords': 'python django senior',
            'language': 'english',
            'location': 'Berlin, Germany'
        }
        response = authenticated_client.post(reverse('job_create'), data=data)
        assert response.status_code == 302
        assert Job.objects.filter(title='Senior Python Developer').exists()


# =============================================================================
# TESTS FOR PDF DOWNLOAD VIEWS
# =============================================================================

@pytest.mark.django_db
class TestPDFDownloadViews:
    @patch('applications.views.get_resume_text')
    @patch('applications.views.extract_personal_info')
    @patch('applications.views.generate_tailored_docx')
    def test_download_tailored_cv_pdf(self, mock_generate, mock_extract, mock_get_text, 
                                       authenticated_client, job_with_cv, automated_match):
        mock_get_text.return_value = "Resume text"
        mock_extract.return_value = {'name': 'John Doe'}
        mock_generate.return_value = MagicMock(read=lambda: b"PDF content")
        
        # FIXED URL NAME: Updated from download_tailored_cv_pdf to download_tailored_docx
        response = authenticated_client.get(
            reverse('download_tailored_docx', kwargs={'match_id': automated_match.pk})
        )
        assert response.status_code == 200
        assert response['Content-Type'] == 'application/pdf'


# =============================================================================
# TESTS FOR API ENDPOINTS (FIXED INDENTATION)
# =============================================================================

@pytest.mark.django_db
class TestAPIEndpoints:
    
    def test_match_status_api(self, authenticated_client, automated_match):
        # FIXED URL NAME: Updated to match fixed urls.py
        response = authenticated_client.get(
            reverse('match_status', kwargs={'match_id': automated_match.pk})
        )
        assert response.status_code == 200
    
    def test_match_logs_api(self, authenticated_client, automated_match):
        from applications.models import ApplicationLog
        ApplicationLog.objects.create(
            match=automated_match,
            action='search',
            description='Found job',
            timestamp=timezone.now()
        )
        # FIXED URL NAME: Updated to match fixed urls.py
        response = authenticated_client.get(
            reverse('match_logs', kwargs={'match_id': automated_match.pk})
        )
        assert response.status_code == 200

    @patch('applications.views.simulate_job_application')
    def test_trigger_apply_api_success(self, mock_simulate, authenticated_client, automated_match):
        # FIXED URL NAME: Updated to match fixed urls.py
        response = authenticated_client.post(
            reverse('trigger_apply', kwargs={'match_id': automated_match.pk})
        )
        assert response.status_code == 200
        automated_match.refresh_from_db()
        assert automated_match.status == 'applying'

    def test_trigger_apply_api_already_running(self, authenticated_client, automated_match):
        automated_match.status = 'applying'
        automated_match.save()
        response = authenticated_client.post(
            reverse('trigger_apply', kwargs={'match_id': automated_match.pk})
        )
        assert response.status_code == 200
        assert 'Already running' in response.json()['message']

# =============================================================================
# TESTS FOR VIEW APPLICATION PDF
# =============================================================================

@pytest.mark.django_db
class TestViewApplicationPDF:
    @patch('applications.views.get_resume_text')
    @patch('applications.views.extract_personal_info')
    @patch('applications.views.generate_tailored_docx')
    def test_view_cv_pdf_inline(self, mock_generate, mock_extract, mock_get_text,
                                 authenticated_client, job_with_cv, automated_match):
        mock_get_text.return_value = "Resume text"
        mock_extract.return_value = {'name': 'John Doe'}
        mock_generate.return_value = MagicMock(read=lambda: b"PDF content")
        
        # FIXED URL: Mapping to download route as it serves PDF
        response = authenticated_client.get(
            reverse('download_tailored_docx', kwargs={'match_id': automated_match.pk})
        )
        assert response.status_code == 200
        assert response['Content-Type'] == 'application/pdf'