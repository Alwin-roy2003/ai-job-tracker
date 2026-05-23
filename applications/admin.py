from django.contrib import admin
from .models import (
    About, 
    Contact, 
    UserProfile, 
    Job, 
    AutomatedJobMatch, 
    PlatformConfig, 
    ApplicationLog,
    ResumeVersion,
    EmailNotification
)

# ==========================================
# 1. PLATFORM CONFIGURATION (CRITICAL)
# ==========================================
@admin.register(PlatformConfig)
class PlatformConfigAdmin(admin.ModelAdmin):
    """
    Manages your Login Credentials and Gmail OTP settings.
    This is where you enter your LinkedIn Mail ID and App Password.
    """
    list_display = ('user', 'platform', 'username', 'email_for_otp', 'is_active')
    list_filter = ('platform', 'is_active')
    search_fields = ('username', 'email_for_otp')
    fieldsets = (
        ('User & Platform', {
            'fields': ('user', 'platform', 'is_active')
        }),
        ('Login Credentials', {
            'fields': ('username', 'password'),
            'description': "Username should be your Mail ID for the platform."
        }),
        ('Auto-OTP Settings (Gmail)', {
            'fields': ('email_for_otp', 'email_app_password', 'imap_server'),
            'description': "Use a Google App Password, NOT your normal Gmail password."
        }),
        ('Limits & Templates', {
            'fields': ('daily_apply_limit', 'search_url_template'),
            'classes': ('collapse',)
        }),
    )

# ==========================================
# 2. AUTOMATION LOGS (LIVE TERMINAL)
# ==========================================
@admin.register(ApplicationLog)
class ApplicationLogAdmin(admin.ModelAdmin):
    """View the real-time history of what the browser bot did"""
    list_display = ('match', 'action', 'timestamp')
    list_filter = ('action', 'timestamp')
    readonly_fields = ('timestamp',)
    ordering = ('-timestamp',)

# ==========================================
# 3. JOB SEARCHES & MATCHES
# ==========================================
@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """Main job search entries"""
    list_display = ('title', 'platform', 'user', 'status', 'created_at')
    list_filter = ('platform', 'status', 'language')
    search_fields = ('title', 'keywords')

@admin.register(AutomatedJobMatch)
class AutomatedJobMatchAdmin(admin.ModelAdmin):
    """Individual job listings found and applied to"""
    list_display = ('company_name', 'job_title', 'status', 'ats_score', 'is_sent')
    list_filter = ('status', 'is_sent', 'created_at')
    search_fields = ('company_name', 'job_title', 'platform_job_id')
    readonly_fields = ('created_at', 'updated_at')

# ==========================================
# 4. CORE WEBSITE & USER CONTENT
# ==========================================
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'location', 'created_at')
    search_fields = ('user__username', 'skills')

@admin.register(About)
class AboutAdmin(admin.ModelAdmin):
    list_display = ('__str__',)

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'created_at')
    readonly_fields = ('created_at',)

@admin.register(ResumeVersion)
class ResumeVersionAdmin(admin.ModelAdmin):
    list_display = ('match', 'version_number', 'ats_score', 'created_at')

@admin.register(EmailNotification)
class EmailNotificationAdmin(admin.ModelAdmin):
    list_display = ('match', 'recipient', 'status', 'sent_at')