from django.db import models
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

# ==========================================
# 1. CORE WEBSITE MODELS
# ==========================================

class About(models.Model):
    description = models.TextField()
    image = models.ImageField(upload_to="about/", blank=True, null=True)

    def __str__(self):
        return "About Section"


class Contact(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.email}"


# ==========================================
# 2. USER PROFILE & SETTINGS
# ==========================================

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=255, blank=True)
    linkedin_url = models.URLField(blank=True)
    portfolio_url = models.URLField(blank=True)
    base_cv_file = models.FileField(upload_to='base_resumes/%Y/%m/', null=True, blank=True)
    base_cv_text = models.TextField(blank=True, help_text="Extracted text from base CV")
    skills = models.JSONField(default=list, help_text="List of user skills")
    preferred_job_titles = models.JSONField(default=list, help_text="Target job titles")
    preferred_locations = models.JSONField(default=list, blank=True)
    email_notifications = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Profile"


# ==========================================
# 3. JOB TRACKING MODELS
# ==========================================

class Job(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('searching', 'Searching'), ('applying', 'Applying'),
        ('applied', 'Applied'), ('interview', 'Interview'), ('offer', 'Offer'),
        ('rejected', 'Rejected'), ('failed', 'Failed'),
    ]
    LANGUAGE_CHOICES = [('english', 'English Language'), ('german', 'German Language')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='jobs')
    title = models.CharField(max_length=255, help_text="e.g. Junior Web Developer")
    platform = models.CharField(max_length=100, help_text="LinkedIn, Indeed, Xing, etc.")
    keywords = models.CharField(max_length=255, help_text="Keywords used to search")
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default='english')
    location = models.CharField(max_length=255, blank=True, help_text="e.g. Berlin, Germany or Munich")
    base_cv = models.FileField(upload_to='resumes/%Y/%m/%d/', null=True, blank=True)
    tailored_content = models.TextField(blank=True, null=True)
    reviewer_name = models.CharField(max_length=100, blank=True, null=True)
    auto_apply = models.BooleanField(default=False)
    headless_mode = models.BooleanField(default=False, help_text="Run browser in headless mode")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # FIX 2: Changed DateField → DateTimeField so timezone.now() comparisons work
    # correctly in views.py and utils.py (DateField vs datetime causes TypeError).
    applied_date = models.DateTimeField(auto_now_add=True)

    # FIX 3: Removed redundant 'created_at' DateField — it was always identical
    # to applied_date since both used auto_now_add=True. Replaced with a proper
    # DateTimeField named created_at for use in ordering and time comparisons.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} at {self.platform}"


class AutomatedJobMatch(models.Model):
    APPLICATION_STATUS = [
        ('found', 'Found'), ('tailoring', 'Tailoring CV'), ('ready', 'Ready to Apply'),
        ('applying', 'Applying'), ('applied', 'Applied'), ('email_sent', 'Email Notification Sent'),
        ('failed', 'Failed'), ('duplicate', 'Duplicate Skipped'),
        # Added 'completed' since views.py filters by status__in=['applied', 'completed']
        ('completed', 'Completed'),
    ]

    job_query = models.ForeignKey('Job', on_delete=models.CASCADE, related_name='matches')
    company_name = models.CharField(max_length=255)

    # FIX 1: job_url is now the reliable unique key used by utils.py's update_or_create.
    # Removed unique_together on platform_job_id because:
    #   a) platform_job_id is nullable — multiple NULLs break the constraint unexpectedly
    #   b) utils.py uses job_url as the lookup key, not platform_job_id
    # Added unique_together on (job_query, job_url) to match actual usage.
    job_url = models.URLField(max_length=1000)

    job_title = models.CharField(max_length=255, blank=True)
    job_description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    tailored_cv_text = models.TextField(help_text="ATS-optimized resume content")
    cover_letter_text = models.TextField(blank=True, null=True)
    ats_score = models.IntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    ats_feedback = models.JSONField(default=dict, blank=True)
    hr_email = models.EmailField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=APPLICATION_STATUS, default='found')
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(blank=True, null=True)
    applied_at = models.DateTimeField(blank=True, null=True, help_text="When the application was actually submitted")
    error_message = models.TextField(blank=True)
    platform_job_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # FIX 1: Use (job_query, job_url) as the unique constraint — this matches
        # how utils.py does update_or_create(job_query=..., job_url=...) lookups.
        unique_together = ['job_query', 'job_url']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.company_name} - {self.job_title}"


# ==========================================
# 4. LOGGING & VERSIONING
# ==========================================

class ResumeVersion(models.Model):
    match = models.ForeignKey(AutomatedJobMatch, on_delete=models.CASCADE, related_name='resume_versions')
    file = models.FileField(upload_to='tailored_resumes/%Y/%m/')
    content_text = models.TextField()
    ats_score = models.IntegerField(default=0)
    version_number = models.PositiveIntegerField(default=1)
    is_downloaded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Resume v{self.version_number} for {self.match.company_name}"


class ApplicationLog(models.Model):
    # FIX 4: Added 'warning' choice — utils.py calls log_event(match, "warning", ...)
    # in many places but 'warning' was missing from ACTION_CHOICES. Without it,
    # the Django admin shows a blank/unknown value for all warning logs.
    ACTION_CHOICES = [
        ('info', 'System Information'),
        ('action', 'Browser Action'),
        ('success', 'Success Achievement'),
        ('warning', 'Warning'),           # ← ADDED
        ('error', 'Critical Error'),
        ('search', 'Searching'),
        ('fill', 'Filling Form'),
        ('submit', 'Submitting'),
    ]

    match = models.ForeignKey(AutomatedJobMatch, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    description = models.TextField()
    screenshot = models.ImageField(upload_to='automation_screenshots/%Y/%m/', blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"[{self.action}] {self.match.company_name} - {self.timestamp.strftime('%H:%M:%S')}"


# ==========================================
# 5. AUTOMATION & PLATFORM CONFIGURATION
# ==========================================

class PlatformConfig(models.Model):
    PLATFORM_CHOICES = [
        ('linkedin', 'LinkedIn'), ('xing', 'Xing'),
        ('stepstone', 'Stepstone'), ('indeed', 'Indeed'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='platform_configs')
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    username = models.CharField(max_length=255, help_text="Login Mail ID for the platform")
    password = models.CharField(max_length=255, help_text="Login Password for the platform")
    email_for_otp = models.EmailField(blank=True, null=True, help_text="alwinroyadat2003@gmail.com")
    email_app_password = models.CharField(max_length=255, blank=True, null=True, help_text="vofyrcqjpfkiemfw")
    imap_server = models.CharField(max_length=100, default="imap.gmail.com")
    daily_apply_limit = models.IntegerField(default=10, help_text="Maximum applications per day")
    search_url_template = models.CharField(max_length=500, blank=True, help_text="Custom search URL template")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'platform']
        verbose_name = "Platform Configuration"
        verbose_name_plural = "Platform Configurations"

    def __str__(self):
        return f"{self.get_platform_display()} config for {self.user.username}"


class EmailNotification(models.Model):
    match = models.OneToOneField(AutomatedJobMatch, on_delete=models.CASCADE, related_name='email_notification')
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    company_name = models.CharField(max_length=255)
    job_role = models.CharField(max_length=255)
    platform = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')], default='pending')
    sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Email for {self.company_name} ({self.status})"