"""
Simplified test file for applications/models.py
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone
from django.contrib.auth import get_user_model
from applications.models import (
    About, Contact, UserProfile, Job, AutomatedJobMatch,
    ResumeVersion, ApplicationLog, PlatformConfig, EmailNotification
)

User = get_user_model()


# =============================================================================
# TESTS FOR About MODEL
# =============================================================================

@pytest.mark.django_db
class TestAboutModel:
    def test_about_creation(self):
        about = About.objects.create(description="Test about section")
        assert about.id is not None
        assert str(about) == "About Section"


# =============================================================================
# TESTS FOR Contact MODEL
# =============================================================================

@pytest.mark.django_db
class TestContactModel:
    def test_contact_creation(self):
        contact = Contact.objects.create(
            name="John Doe",
            email="john@example.com",
            message="Test message"
        )
        assert str(contact) == "John Doe - john@example.com"


# =============================================================================
# TESTS FOR UserProfile MODEL
# =============================================================================

@pytest.mark.django_db
class TestUserProfileModel:
    def test_profile_creation(self):
        user = User.objects.create_user(username='testuser', email='test@test.com', password='pass')
        profile = UserProfile.objects.create(
            user=user,
            phone="+49123456789",
            location="Berlin, Germany"
        )
        assert profile.user == user
        assert str(profile) == "testuser Profile"
    
    def test_profile_one_to_one_relationship(self):
        user = User.objects.create_user(username='testuser2', email='test2@test.com', password='pass')
        UserProfile.objects.create(user=user, phone="123")
        
        with pytest.raises(IntegrityError):
            UserProfile.objects.create(user=user, phone="456")
    
    def test_profile_defaults(self):
        user = User.objects.create_user(username='testuser3', email='test3@test.com', password='pass')
        profile = UserProfile.objects.create(user=user)
        assert profile.email_notifications is True
        assert profile.skills == []


# =============================================================================
# TESTS FOR Job MODEL
# =============================================================================

@pytest.mark.django_db
class TestJobModel:
    def test_job_creation(self):
        user = User.objects.create_user(username='jobuser', email='job@test.com', password='pass')
        job = Job.objects.create(
            user=user,
            title="Python Developer",
            platform="linkedin",
            keywords="python django",
            location="Berlin"
        )
        assert str(job) == "Python Developer at linkedin"
    
    def test_job_status_choices(self):
        user = User.objects.create_user(username='jobuser2', email='job2@test.com', password='pass')
        valid_statuses = ['draft', 'searching', 'applying', 'applied', 'interview', 'offer', 'rejected', 'failed']
        
        for status in valid_statuses:
            job = Job.objects.create(
                user=user,
                title=f"Job {status}",
                platform="linkedin",
                status=status
            )
            assert job.status == status


# =============================================================================
# TESTS FOR PlatformConfig MODEL
# =============================================================================

@pytest.mark.django_db
class TestPlatformConfigModel:
    def test_config_creation(self):
        user = User.objects.create_user(username='configuser', email='config@test.com', password='pass')
        config = PlatformConfig.objects.create(
            user=user,
            platform="linkedin",
            username="user@linkedin.com",
            password="pass"
        )
        assert config.daily_apply_limit == 10
    
    def test_unique_platform_per_user(self):
        user = User.objects.create_user(username='configuser2', email='config2@test.com', password='pass')
        PlatformConfig.objects.create(
            user=user,
            platform="linkedin",
            username="first@linkedin.com",
            password="pass"
        )
        
        with pytest.raises(IntegrityError):
            PlatformConfig.objects.create(
                user=user,
                platform="linkedin",
                username="second@linkedin.com",
                password="pass"
            )