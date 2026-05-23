from rest_framework import serializers
from .models import Job, AutomatedJobMatch


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = '__all__'
        # FIX 2: Added applied_date and status — these are set by the system,
        # not by the user submitting the form.
        read_only_fields = ['user', 'created_at', 'applied_date', 'status']

    # FIX 3: Validate that uploaded CV is PDF or DOCX only.
    def validate_base_cv(self, value):
        if value:
            allowed = ['.pdf', '.docx']
            name = value.name.lower()
            if not any(name.endswith(ext) for ext in allowed):
                raise serializers.ValidationError(
                    "Only PDF and DOCX files are accepted."
                )
            # Also cap file size at 5MB
            if value.size > 5 * 1024 * 1024:
                raise serializers.ValidationError(
                    "CV file size must be under 5MB."
                )
        return value


class JobSummarySerializer(serializers.ModelSerializer):
    """Lightweight serializer for nested use — no large fields."""
    class Meta:
        model = Job
        fields = ['id', 'title', 'platform', 'location', 'status']


class AutomatedJobMatchSerializer(serializers.ModelSerializer):
    # FIX 4: Nest a lightweight job summary instead of just returning the raw ID.
    job_query = JobSummarySerializer(read_only=True)

    class Meta:
        model = AutomatedJobMatch
        # FIX 1: Explicit field list instead of '__all__' to avoid returning
        # large tailored_cv_text and cover_letter_text in list views.
        # Use a separate detail endpoint if full CV text is needed.
        fields = [
            'id', 'job_query', 'company_name', 'job_url', 'job_title',
            'location', 'ats_score', 'status', 'applied_at', 'created_at',
            # Excluded from list views for performance:
            # 'tailored_cv_text', 'cover_letter_text', 'ats_feedback'
        ]
        read_only_fields = ['created_at', 'updated_at', 'applied_at']


class AutomatedJobMatchDetailSerializer(AutomatedJobMatchSerializer):
    """Full serializer for detail view — includes CV and cover letter text."""
    class Meta(AutomatedJobMatchSerializer.Meta):
        fields = AutomatedJobMatchSerializer.Meta.fields + [
            'tailored_cv_text', 'cover_letter_text', 'ats_feedback',
            'error_message', 'is_sent', 'hr_email'
        ]