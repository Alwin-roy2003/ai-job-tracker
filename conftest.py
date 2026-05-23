# ==========================================
# conftest.py
# ==========================================
# FIX 1 & 4: Removed manual django.setup(), sys.path.insert(), and
# os.environ.setdefault() — pytest-django handles all of this automatically
# via pytest.ini (see below). Manual setup causes double-init conflicts
# that break test isolation.

import pytest
from django.contrib.auth import get_user_model
from applications.models import Job, AutomatedJobMatch, PlatformConfig

User = get_user_model()


@pytest.fixture
def user(db):
    """Create a test user"""
    return User.objects.create_user(
        username='testuser',
        email='test@example.com',
        password='testpass123'
    )


@pytest.fixture
def user_profile(db, user):
    """Create a test user profile"""
    from applications.models import UserProfile
    return UserProfile.objects.create(
        user=user,
        phone='+49123456789',
        location='Berlin, Germany',
        skills=['Python', 'Django', 'React'],
        preferred_job_titles=['Full Stack Developer'],
        preferred_locations=['Berlin', 'Munich']
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
def platform_config(db, user):
    """Create test platform config"""
    return PlatformConfig.objects.create(
        user=user,
        platform='linkedin',
        username='test@gmail.com',
        password='testpass',
        # FIX 3: Added email_for_otp — present in the model, good to include
        # in test fixtures for completeness and to test email verification flows.
        email_for_otp='test@gmail.com',
        email_app_password='16digitcode',
        daily_apply_limit=10
    )


@pytest.fixture
def automated_match(db, job):
    """Create test job match"""
    return AutomatedJobMatch.objects.create(
        job_query=job,
        company_name='Test Corp',
        job_url='https://linkedin.com/jobs/123',
        job_title='Python Developer',
        job_description='Looking for Python dev',
        location='Berlin, Germany',
        tailored_cv_text='Tailored resume content',
        ats_score=90,
        platform_job_id='linkedin_12345'
    )