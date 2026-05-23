import os
from rest_framework import generics, permissions, status, parsers
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
import google.generativeai as genai
from PyPDF2 import PdfReader
from docx import Document
import json
import re

from .models import Job, AutomatedJobMatch, UserProfile
from .serializers import JobSerializer, AutomatedJobMatchSerializer
from .utils import get_resume_text

# Configure Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# FIX 1: gemini-pro was deprecated and shut down in early 2025.
# Use gemini-1.5-flash (fast + cheap) or gemini-1.5-pro (higher quality).
# gemini-1.5-flash is recommended here for speed and cost efficiency.
GEMINI_MODEL = "gemini-1.5-flash"


class JobListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = JobSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        return Job.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        job = serializer.save(user=self.request.user)
        
        # Trigger AI processing if base_cv exists
        if job.base_cv:
            self.process_job_with_ai(job)

    def process_job_with_ai(self, job):
        """
        1. Extract text from base CV
        2. Generate optimized keywords for job search
        3. Store tailored content preview
        NOTE: This runs synchronously — consider moving to a Celery task
        if Gemini responses are slow and causing request timeouts.
        """
        try:
            resume_text = self.extract_text_from_file(job.base_cv.path)
            
            if resume_text and GEMINI_API_KEY:
                keywords_prompt = f"""
                Based on this resume, generate 5 optimized search keywords for finding 
                {job.title} jobs on {job.platform}. Return only comma-separated keywords.
                
                Resume: {resume_text[:2000]}...
                """
                
                # FIX 1: Use GEMINI_MODEL constant instead of hardcoded 'gemini-pro'
                model = genai.GenerativeModel(GEMINI_MODEL)
                response = model.generate_content(keywords_prompt)
                
                # FIX 2: Guard against None response.text before using it
                if response.text:
                    job.keywords = response.text.strip()
                    job.tailored_content = f"Search keywords optimized for {job.title}"
                    job.save()
                    print(f"[AI] Keywords generated for Job {job.id}")
                else:
                    print(f"[WARNING] Gemini returned empty response for Job {job.id}")
                    
        except Exception as e:
            print(f"[ERROR] AI Processing failed: {e}")

    def extract_text_from_file(self, file_path):
        """Extract text from PDF or DOCX"""
        text = ""
        try:
            if file_path.endswith('.pdf'):
                reader = PdfReader(file_path)
                for page in reader.pages:
                    text += page.extract_text() or ""
            elif file_path.endswith('.docx'):
                doc = Document(file_path)
                text = "\n".join([para.text for para in doc.paragraphs])
            return text
        except Exception as e:
            print(f"[ERROR] File extraction: {e}")
            return ""


class JobDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = JobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Job.objects.filter(user=self.request.user)


class JobMatchListAPIView(generics.ListAPIView):
    """List all AI-matched jobs for a specific Job query"""
    serializer_class = AutomatedJobMatchSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        job_id = self.kwargs.get('job_id')
        return AutomatedJobMatch.objects.filter(
            job_query_id=job_id,
            job_query__user=self.request.user
        ).order_by('-ats_score')


class JobMatchDetailAPIView(generics.RetrieveAPIView):
    """Get details of a specific job match with tailored content"""
    serializer_class = AutomatedJobMatchSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        return AutomatedJobMatch.objects.filter(job_query__user=self.request.user)


class GenerateTailoredContentAPIView(generics.GenericAPIView):
    """
    POST /api/jobs/<job_id>/matches/<match_id>/generate/
    Generates ATS-optimized CV and cover letter for a specific job match.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, job_id, match_id):
        match = get_object_or_404(
            AutomatedJobMatch,
            id=match_id,
            job_query_id=job_id,
            job_query__user=request.user
        )
        
        try:
            # FIX 3: Try UserProfile.base_cv_text first, then fall back to the
            # CV file uploaded directly on the Job object. Old code returned a
            # 400 error even when a CV file was available on the job.
            base_cv_text = ""

            user_profile = UserProfile.objects.filter(user=request.user).first()
            if user_profile and user_profile.base_cv_text:
                base_cv_text = user_profile.base_cv_text
            elif match.job_query.base_cv:
                try:
                    base_cv_text = get_resume_text(match.job_query.base_cv.path)
                except Exception as e:
                    print(f"[WARNING] Could not read CV file: {e}")

            if not base_cv_text:
                return Response(
                    {"error": "No base CV found. Please upload a CV first."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Generate Tailored CV
            tailored_cv = self.generate_tailored_cv(
                base_cv_text,
                match.job_description,
                match.company_name,
                match.job_query.title
            )

            # FIX 2: Guard against None from Gemini before saving or slicing
            if not tailored_cv:
                return Response(
                    {"error": "CV generation failed — Gemini returned an empty response."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Generate Cover Letter
            cover_letter = self.generate_cover_letter(
                match.company_name,
                match.job_title,
                match.job_description,
                base_cv_text
            )

            if not cover_letter:
                return Response(
                    {"error": "Cover letter generation failed — Gemini returned an empty response."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Calculate ATS Score
            ats_score, ats_feedback = self.calculate_ats_score(
                tailored_cv,
                match.job_description
            )

            # Save to match
            match.tailored_cv_text = tailored_cv
            match.cover_letter_text = cover_letter
            match.ats_score = ats_score
            match.ats_feedback = ats_feedback
            match.status = 'ready'
            match.save()

            return Response({
                "message": "Content generated successfully",
                "ats_score": ats_score,
                "ats_feedback": ats_feedback,
                "tailored_cv_preview": tailored_cv[:500] + "...",
                "cover_letter_preview": cover_letter[:300] + "..."
            })

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def generate_tailored_cv(self, base_cv, job_description, company, job_title):
        """Use Gemini to tailor CV for specific job"""
        if not GEMINI_API_KEY:
            raise Exception("Gemini API key not configured")

        prompt = f"""
        Rewrite this CV to be ATS-optimized for the following job. 
        Ensure ATS score of 90+ by including relevant keywords naturally.
        
        Job Title: {job_title}
        Company: {company}
        Job Description: {job_description}
        
        Original CV:
        {base_cv[:3000]}
        
        Provide a professional CV with:
        1. Professional Summary (tailored to job)
        2. Skills (ATS-optimized keywords)
        3. Experience (quantified achievements matching job requirements)
        4. Education
        
        Format in clean text with clear sections.
        """

        # FIX 1: Use GEMINI_MODEL instead of hardcoded 'gemini-pro'
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)

        # FIX 2: Return None safely if response is empty/blocked
        return response.text if response.text else None

    def generate_cover_letter(self, company, job_title, job_description, user_cv):
        """Generate personalized cover letter"""
        if not GEMINI_API_KEY:
            raise Exception("Gemini API key not configured")

        prompt = f"""
        Write a professional, personalized cover letter for this position:
        
        Company: {company}
        Role: {job_title}
        Job Description: {job_description[:1000]}
        
        My Background: {user_cv[:1500]}
        
        Requirements:
        - No generic language
        - Company-specific
        - Role-specific  
        - Professional yet personable tone
        - 3-4 paragraphs max
        - Mention specific alignment between my skills and job requirements
        """

        # FIX 1: Use GEMINI_MODEL instead of hardcoded 'gemini-pro'
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)

        # FIX 2: Return None safely if response is empty/blocked
        return response.text if response.text else None

    def calculate_ats_score(self, cv_text, job_description):
        """
        Calculate ATS compatibility score and provide feedback.
        Returns: (score_int, feedback_dict)
        """
        if not GEMINI_API_KEY:
            return 0, {}

        prompt = f"""
        Analyze this CV against the job description and provide an ATS score (0-100).
        
        Job Description: {job_description[:2000]}
        
        CV: {cv_text[:2000]}
        
        Return ONLY a JSON object in this exact format with no extra text or markdown:
        {{
            "score": <number 0-100>,
            "keyword_match": "<percentage>",
            "formatting_issues": ["issue1", "issue2"],
            "missing_keywords": ["keyword1", "keyword2"],
            "suggestions": ["suggestion1", "suggestion2"]
        }}
        
        Ensure score is 90+ for good matches.
        """

        try:
            # FIX 1: Use GEMINI_MODEL instead of hardcoded 'gemini-pro'
            model = genai.GenerativeModel(GEMINI_MODEL)
            response = model.generate_content(prompt)

            # FIX 2: Guard against None response
            if not response.text:
                return 75, {"note": "Score estimated — Gemini returned empty response"}

            # FIX 4: Strip markdown code fences before parsing JSON.
            # Old regex r'\{.*\}' greedily matched from the first '{' to the last '}'
            # across the whole string, capturing garbage if Gemini wrapped the JSON
            # in ```json ... ``` or added explanatory text with braces.
            raw = response.text.strip()
            # Remove ```json ... ``` or ``` ... ``` wrappers
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

            # Now find the first complete JSON object
            json_match = re.search(r'\{.*?\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                score = min(100, max(0, int(result.get('score', 0))))
                return score, result
            else:
                return 75, {"note": "Score estimated — could not parse JSON from response"}

        except json.JSONDecodeError as e:
            print(f"[ATS JSON Error] {e}")
            return 75, {"error": f"JSON parse error: {str(e)}"}
        except Exception as e:
            print(f"[ATS Error] {e}")
            return 0, {"error": str(e)}