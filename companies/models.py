from django.db import models


class Company(models.Model):
    PLATFORM_TYPES = [
        ('social', 'Social Media (LinkedIn/Xing)'),
        ('board', 'Job Board (Indeed/Stepstone)'),
        ('app', 'Job Search App'),
        ('direct', 'Direct Company Website'),
        ('agency', 'Recruitment Agency'),
    ]

    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="e.g. LinkedIn, Indeed, Xing, or Amazon"
    )
    platform_type = models.CharField(
        max_length=20,
        choices=PLATFORM_TYPES,
        default='board'
    )

    # FIX 1: Restored URLField — TextField accepts any string including
    # invalid URLs. URLField validates the format properly.
    website = models.URLField(
        blank=True,
        null=True,
        help_text="URL of the platform or company career page"
    )

    # FIX 2: Removed null=True — for text fields Django convention is to use
    # blank=True + default='' only. Having both null and blank creates two
    # possible empty states (NULL and "") which complicates queries.
    location = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Main office or region (e.g. Berlin, Munich, Remote)"
    )

    notes = models.TextField(
        blank=True,
        default="",
        help_text="Specific search filters or login reminders for this platform"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    # FIX 3: Added updated_at — useful to track when platform configs change
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform / Company"
        verbose_name_plural = "Platforms & Companies"

    def __str__(self):
        return f"{self.name} ({self.get_platform_type_display()})"