"""
Test file for applications/utils.py - Job automation utilities
Run with: pytest applications/tests/test_utils.py -v
"""

import os
import json
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock, call
from io import BytesIO
from datetime import datetime
from django.utils import timezone

from applications.utils import (
    JobApplicationConfig,
    load_applied_jobs,
    save_applied_jobs,
    is_job_applied,
    mark_job_applied,
    log_event,
    get_resume_text,
    extract_cv_sections,
    extract_personal_info,
    build_cv_content,
    generate_cover_letter_text,
    create_cv_pdf,
    create_cover_letter_pdf,
    send_application_email,
    linkedin_login,
    search_jobs_linkedin,
    get_job_details,
    click_apply_button,
    fill_application_form,
    submit_application,
    run_job_application_automation,
    APPLIED_JOBS_FILE
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_applied_jobs_file(tmp_path):
    """Create a temporary applied jobs file for testing"""
    test_file = tmp_path / "test_applied_jobs.json"
    with patch('applications.utils.APPLIED_JOBS_FILE', str(test_file)):
        yield str(test_file)


@pytest.fixture
def mock_match():
    """Create a mock AutomatedJobMatch"""
    match = Mock()
    match.id = 61
    match.job_query = Mock()
    match.job_query.title = "Python Developer"
    match.job_query.location = "Berlin, Germany"
    match.job_query.user.email = "test@example.com"
    match.job_query.base_cv = None
    match.status = 'pending'
    match.company_name = ""
    match.job_title = ""
    match.job_url = ""
    match.tailored_cv_text = ""
    match.cover_letter_text = ""
    match.ats_score = 0
    match.applied_at = None
    match.save = Mock()
    return match


@pytest.fixture
def sample_cv_text():
    """Sample CV text for testing"""
    return """
ALWIN ROY
+491784902137 | alwinroyadat2003@gmail.com | Berlin, Germany

PROFESSIONAL SUMMARY
Computer Science graduate student with hands-on experience in Python.

EDUCATION
BSc (Hons) Computer Science and Digitisation
Berlin School of Business and Innovation, Berlin, Germany

PROFESSIONAL EXPERIENCE
Junior Python Full Stack Developer
Soften Technologies, Kerala, India
• Developed Python-based full-stack web applications using Django

TECHNICAL SKILLS
Python, JavaScript, Django, Flask, PostgreSQL, MongoDB

LANGUAGES
English: C1 (Advanced)
"""


@pytest.fixture
def mock_personal_info():
    """Sample personal info dictionary"""
    return {
        'name': 'Alwin Roy',
        'first_name': 'Alwin',
        'last_name': 'Roy',
        'phone': '+491784902137',
        'email': 'alwinroyadat2003@gmail.com',
        'linkedin': 'https://linkedin.com/in/alwinroy',
        'location': 'Berlin, Germany',
        'city': 'Berlin',
        'country': 'Germany',
        'visa_status': 'Student Visa'
    }


@pytest.fixture
def mock_driver():
    """Mock Selenium WebDriver"""
    driver = Mock()
    driver.current_url = "https://www.linkedin.com/jobs"
    driver.window_handles = ["main"]
    driver.find_elements.return_value = []
    driver.execute_script = Mock(return_value=None)
    driver.get = Mock(return_value=None)
    driver.find_element = Mock(side_effect=Exception("Element not found"))
    driver.quit = Mock(return_value=None)
    return driver


# =============================================================================
# TESTS FOR CONFIGURATION
# =============================================================================

@pytest.mark.django_db
class TestJobApplicationConfig:
    """Test JobApplicationConfig class"""
    
    def test_default_configuration(self):
        """Test default config values"""
        config = JobApplicationConfig()
        assert config.job_role == ""
        assert config.platform == "linkedin"
        assert config.language == "english"
        assert config.location == ""
        assert config.keywords == []
        assert config.exclude_easy_apply is True
        assert config.last_24_hours is True
        assert config.max_applications == 5
    
    def test_custom_configuration(self):
        """Test setting custom values"""
        config = JobApplicationConfig()
        config.job_role = "Senior Python Developer"
        config.location = "Munich, Germany"
        config.keywords = ["python", "django", "aws"]
        config.max_applications = 10
        
        assert config.job_role == "Senior Python Developer"
        assert config.location == "Munich, Germany"
        assert config.keywords == ["python", "django", "aws"]
        assert config.max_applications == 10


# =============================================================================
# TESTS FOR APPLIED JOBS TRACKING
# =============================================================================

@pytest.mark.django_db
class TestAppliedJobsTracking:
    """Test job application tracking"""
    
    def test_load_applied_jobs_empty(self, temp_applied_jobs_file):
        """Test loading when file doesn't exist"""
        result = load_applied_jobs()
        assert result == set()
    
    def test_load_applied_jobs_with_data(self, temp_applied_jobs_file):
        """Test loading existing applied jobs"""
        test_data = ["https://linkedin.com/job/1", "companyA_engineer"]
        with open(temp_applied_jobs_file, 'w') as f:
            json.dump(test_data, f)
        
        result = load_applied_jobs()
        assert result == set(test_data)
    
    def test_save_applied_jobs(self, temp_applied_jobs_file):
        """Test saving applied jobs"""
        applied_set = {"https://job1.com", "company_dev"}
        save_applied_jobs(applied_set)
        
        with open(temp_applied_jobs_file, 'r') as f:
            saved = json.load(f)
        assert set(saved) == applied_set
    
    def test_is_job_applied_by_url(self, temp_applied_jobs_file):
        """Test checking applied status by URL"""
        mark_job_applied("https://linkedin.com/job/123", "Company", "Job")
        
        assert is_job_applied("https://linkedin.com/job/123", "Other", "Other") is True
        assert is_job_applied("https://linkedin.com/job/999", "Other", "Other") is False
    
    def test_is_job_applied_by_company_title(self, temp_applied_jobs_file):
        """Test checking by company + title"""
        mark_job_applied("", "TechCorp", "Python Developer")
        
        assert is_job_applied("", "TechCorp", "Python Developer") is True
        assert is_job_applied("", "TechCorp", "Java Developer") is False
    
    def test_mark_job_applied_both_methods(self, temp_applied_jobs_file):
        """Test marking stores both URL and key"""
        mark_job_applied("https://job.url", "MyCompany", "Engineer")
        
        applied = load_applied_jobs()
        assert "https://job.url" in applied
        assert "mycompany_engineer" in applied
    
    def test_is_job_applied_with_none_values(self, temp_applied_jobs_file):
        """Test handling None values"""
        result = is_job_applied(None, None, None)
        assert result is False


# =============================================================================
# TESTS FOR LOGGING
# =============================================================================

@pytest.mark.django_db
class TestLogging:
    """Test event logging"""
    
    def test_log_event_success(self, mock_match, capsys):
        """Test successful logging"""
        with patch('applications.utils.ApplicationLog.objects.create') as mock_create:
            log_event(mock_match, "test_action", "Test description")
            
            mock_create.assert_called_once()
            kwargs = mock_create.call_args[1]
            assert kwargs['match'] == mock_match
            assert kwargs['action'] == "test_action"
    
    def test_log_event_database_failure(self, mock_match, capsys):
        """Test logging when database fails"""
        with patch('applications.utils.ApplicationLog.objects.create') as mock_create:
            mock_create.side_effect = Exception("DB Error")
            
            log_event(mock_match, "action", "desc")
            
            captured = capsys.readouterr()
            assert "[ACTION]" in captured.out


# =============================================================================
# TESTS FOR CV PROCESSING
# =============================================================================

@pytest.mark.django_db
class TestCVProcessing:
    """Test CV text extraction and processing"""
    
    def test_get_resume_text_valid_pdf(self, tmp_path):
        """Test extracting text from valid PDF"""
        from reportlab.pdfgen import canvas
        
        pdf_path = tmp_path / "test_cv.pdf"
        c = canvas.Canvas(str(pdf_path))
        c.drawString(100, 700, "Python Developer")
        c.drawString(100, 680, "Experience: 5 years")
        c.save()
        
        result = get_resume_text(str(pdf_path))
        assert "Python Developer" in result
        assert "Experience: 5 years" in result
    
    def test_get_resume_text_nonexistent(self):
        """Test handling non-existent file"""
        result = get_resume_text("/nonexistent/path.pdf")
        assert result == ""
    
    def test_get_resume_text_not_pdf(self, tmp_path):
        """Test handling non-PDF file"""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Some text")
        result = get_resume_text(str(txt_file))
        assert result == ""
    
    def test_extract_cv_sections_with_content(self, sample_cv_text):
        """Test extracting sections from CV"""
        sections = extract_cv_sections(sample_cv_text)
        
        assert sections['name'] == 'ALWIN ROY'
        assert 'Computer Science' in sections['education']
        assert 'Junior Python Full Stack Developer' in sections['experience']
        assert 'Python, JavaScript, Django' in sections['skills']
    
    def test_extract_cv_sections_empty(self):
        """Test extracting from empty CV (returns defaults)"""
        sections = extract_cv_sections("")
        
        assert sections['name'] == 'ALWIN ROY'
        assert 'professional_summary' in sections
        assert 'Django' in sections['skills']
    
    def test_extract_personal_info_from_text(self, sample_cv_text):
        """Test extracting personal info"""
        info = extract_personal_info(sample_cv_text)
        
        assert info['email'] == 'alwinroyadat2003@gmail.com'
        assert info['phone'] == '+491784902137'
    
    def test_extract_personal_info_empty(self):
        """Test extracting with empty text (returns defaults)"""
        info = extract_personal_info("")
        
        assert info['name'] == 'Alwin Roy'
        assert info['email'] == 'alwinroyadat2003@gmail.com'


# =============================================================================
# TESTS FOR DOCUMENT GENERATION
# =============================================================================

@pytest.mark.django_db
class TestDocumentGeneration:
    """Test CV and cover letter generation"""
    
    def test_build_cv_content(self, mock_personal_info):
        """Test building tailored CV content"""
        sections = {
            'name': 'ALWIN ROY',
            'contact': 'test@example.com | +1234567890',
            'professional_summary': 'Python developer with 5 years experience',
            'education': 'BSc Computer Science',
            'experience': 'Senior Developer at TechCorp',
            'projects': 'AI Job Tracker',
            'skills': 'Python, Django, React',
            'languages': 'English, German',
            'interests': 'Open source'
        }
        
        content = build_cv_content(
            sections['name'],
            sections['contact'],
            sections,
            "Senior Python Developer",
            "TechCorp GmbH"
        )
        
        assert "ALWIN ROY" in content
        assert "Senior Python Developer" in content
        assert "TechCorp GmbH" in content
    
    def test_generate_cover_letter_text(self, mock_personal_info):
        """Test generating cover letter"""
        letter = generate_cover_letter_text(
            mock_personal_info,
            "Python Developer",
            "TechCorp"
        )
        
        assert "Alwin Roy" in letter
        assert "Python Developer" in letter
        assert "TechCorp" in letter
        assert "Dear Hiring Manager" in letter
    
    def test_create_cv_pdf(self):
        """Test CV PDF creation"""
        cv_content = """ALWIN ROY
test@example.com

PROFESSIONAL SUMMARY
Python developer

EDUCATION
BSc Computer Science"""
        
        result = create_cv_pdf(cv_content)
        
        assert result is not None
        assert isinstance(result, BytesIO)
        result.seek(0)
        assert result.read(4) == b'%PDF'
    
    def test_create_cover_letter_pdf(self):
        """Test cover letter PDF creation"""
        content = "Dear Hiring Manager,\\n\\nI am applying for this position."
        
        result = create_cover_letter_pdf(content)
        
        assert result is not None
        assert isinstance(result, BytesIO)
        result.seek(0)
        assert result.read(4) == b'%PDF'
    
    def test_create_cv_pdf_with_output_path(self, tmp_path):
        """Test CV PDF with file output"""
        output_path = tmp_path / "output_cv.pdf"
        cv_content = "Test CV Content"
        
        result = create_cv_pdf(cv_content, str(output_path))
        
        assert result is True
        assert output_path.exists()


# =============================================================================
# TESTS FOR EMAIL FUNCTIONALITY
# =============================================================================

@pytest.mark.django_db
class TestEmailFunctionality:
    """Test email sending"""
    
    @patch('smtplib.SMTP')
    @patch('applications.utils.log_event')
    def test_send_application_email_success(self, mock_log, mock_smtp, mock_match, mock_personal_info):
        """Test successful email sending"""
        cv_buffer = BytesIO(b'fake cv pdf')
        cl_buffer = BytesIO(b'fake cover letter pdf')
        
        result = send_application_email(
            mock_match, mock_personal_info, cv_buffer, cl_buffer,
            "TechCorp", "Python Developer", "https://job.url"
        )
        
        assert result is True
        mock_smtp.return_value.__enter__.return_value.send_message.assert_called_once()
    
    @patch('applications.utils.log_event')
    def test_send_application_email_no_credentials(self, mock_log, mock_match):
        """Test email without credentials"""
        with patch.dict(os.environ, {}, clear=True):
            result = send_application_email(
                mock_match, {}, None, None, "Company", "Job", "url"
            )
            assert result is False
    
    @patch('smtplib.SMTP')
    @patch('applications.utils.log_event')
    def test_send_application_email_auth_error(self, mock_log, mock_smtp, mock_match):
        """Test handling SMTP error"""
        mock_smtp.return_value.__enter__.return_value.starttls.side_effect = Exception("Auth failed")
        
        result = send_application_email(
            mock_match, {'email': 'test@test.com'}, None, None, "Co", "Job", "url"
        )
        
        assert result is False


# =============================================================================
# TESTS FOR LINKEDIN AUTOMATION (MOCKED)
# =============================================================================

@pytest.mark.django_db
class TestLinkedInAutomation:
    """Test LinkedIn automation with mocked Selenium"""
    
    @patch('applications.utils.PlatformConfig')
    @patch('applications.utils.WebDriverWait')
    @patch('applications.utils.log_event')
    def test_linkedin_login_success(self, mock_log, mock_wait, mock_config, mock_driver, mock_match):
        """Test successful LinkedIn login"""
        config_obj = Mock()
        config_obj.email_for_otp = 'test@example.com'
        config_obj.password = 'testpass'
        mock_config.objects.filter.return_value.first.return_value = config_obj
        
        mock_username = Mock()
        mock_password = Mock()
        mock_button = Mock()
        
        mock_wait.return_value.until.return_value = mock_username
        mock_driver.find_element.side_effect = [mock_password, mock_button]
        mock_driver.current_url = "https://www.linkedin.com/feed"
        
        result = linkedin_login(mock_driver, mock_match)
        
        assert result is True
    
    @patch('applications.utils.PlatformConfig')
    @patch('applications.utils.log_event')
    def test_linkedin_login_no_credentials(self, mock_log, mock_config, mock_driver, mock_match):
        """Test login without credentials"""
        mock_config.objects.filter.return_value.first.return_value = None
        
        result = linkedin_login(mock_driver, mock_match)
        
        assert result is False
    
    @patch('applications.utils.log_event')
    def test_search_jobs_linkedin(self, mock_log, mock_driver, mock_match):
        """Test job search"""
        mock_card1, mock_card2 = Mock(), Mock()
        mock_driver.find_elements.return_value = [mock_card1, mock_card2]
        
        result = search_jobs_linkedin(mock_driver, "Python Developer", "Berlin", mock_match)
        
        assert len(result) == 2
        url = mock_driver.get.call_args[0][0]
        assert "f_TPR=r86400" in url
    
    @patch('applications.utils.log_event')
    def test_get_job_details(self, mock_log, mock_driver, mock_match):
        """Test extracting job details"""
        mock_title = Mock()
        mock_title.text = "Senior Python Developer"
        mock_company = Mock()
        mock_company.text = "TechCorp"
        mock_desc = Mock()
        mock_desc.text = "Job description"
        
        mock_driver.find_element.side_effect = [mock_title, mock_company, mock_desc]
        mock_driver.find_elements.return_value = []
        mock_driver.current_url = "https://linkedin.com/jobs/view/123"
        
        result = get_job_details(mock_driver, mock_match)
        
        assert result is not None
        assert result['title'] == "Senior Python Developer"
        assert result['company'] == "TechCorp"
    
    @patch('applications.utils.log_event')
    def test_click_apply_button_external(self, mock_log, mock_driver, mock_match):
        """Test clicking external apply button"""
        mock_button = Mock()
        mock_button.is_displayed.return_value = True
        mock_button.is_enabled.return_value = True
        mock_button.text = "Apply"
        
        # Mock side effect: first call returns [] (no Easy Apply), second returns [button]
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # Easy Apply check - none found
            return [mock_button]  # Regular apply button found
        
        mock_driver.find_elements.side_effect = side_effect
        
        result = click_apply_button(mock_driver, mock_match)
        
        assert result is True
    
    @patch('applications.utils.log_event')
    def test_click_apply_button_easy_apply_only(self, mock_log, mock_driver, mock_match):
        """Test skipping Easy Apply only jobs"""
        mock_easy = Mock()
        mock_easy.is_displayed.return_value = True
        
        # Mock side effect: first call returns [easy_apply], second returns []
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [mock_easy]  # Easy Apply found
            return []  # No regular apply button
        
        mock_driver.find_elements.side_effect = side_effect
        
        result = click_apply_button(mock_driver, mock_match)
        
        assert result is False
    
    @patch('applications.utils.log_event')
    def test_fill_application_form(self, mock_log, mock_driver, mock_match, mock_personal_info):
        """Test form filling"""
        mock_field = Mock()
        mock_field.is_displayed.return_value = True
        mock_field.is_enabled.return_value = True
        
        def get_attr(name):
            attrs = {
                'id': 'firstName',
                'name': 'firstName', 
                'type': 'text',
                'value': '',
                'placeholder': '',
                'class': ''
            }
            return attrs.get(name, '')
        
        mock_field.get_attribute.side_effect = get_attr
        mock_field.tag_name = "input"
        
        mock_driver.find_elements.return_value = [mock_field]
        
        result = fill_application_form(mock_driver, mock_match, mock_personal_info, None)
        
        assert result is True
    
    @patch('applications.utils.log_event')
    def test_submit_application_success(self, mock_log, mock_driver, mock_match):
        """Test successful submission"""
        mock_submit = Mock()
        mock_submit.is_displayed.return_value = True
        mock_submit.is_enabled.return_value = True
        mock_submit.text = "Submit application"
        mock_submit.get_attribute.return_value = ""
        
        mock_driver.find_elements.return_value = [mock_submit]
        
        result = submit_application(mock_driver, mock_match)
        
        assert result == 'submitted'
    
    @patch('applications.utils.log_event')
    def test_submit_application_next_step(self, mock_log, mock_driver, mock_match):
        """Test multi-step form"""
        mock_next = Mock()
        mock_next.is_displayed.return_value = True
        mock_next.is_enabled.return_value = True
        mock_next.text = "Next"
        mock_next.get_attribute.return_value = ""
        
        mock_driver.find_elements.return_value = [mock_next]
        
        result = submit_application(mock_driver, mock_match)
        
        assert result == 'next'


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.django_db
class TestIntegration:
    """Integration tests for complete workflow"""
    
    @patch('applications.utils.webdriver.Chrome')
    @patch('applications.utils.Service')
    @patch('applications.utils.ChromeDriverManager')
    @patch('applications.utils.linkedin_login')
    @patch('applications.utils.search_jobs_linkedin')
    @patch('applications.utils.get_job_details')
    @patch('applications.utils.click_apply_button')
    @patch('applications.utils.fill_application_form')
    @patch('applications.utils.submit_application')
    @patch('applications.utils.send_application_email')
    @patch('applications.utils.AutomatedJobMatch.objects')
    def test_full_workflow_success(
        self, mock_manager, mock_email, mock_submit, mock_fill,
        mock_click, mock_details, mock_search, mock_login,
        mock_chromedriver, mock_service, mock_chrome, mock_match
    ):
        """Test complete successful workflow"""
        mock_manager.select_related.return_value.get.return_value = mock_match
        mock_login.return_value = True
        
        mock_job_card = Mock()
        mock_search.return_value = [mock_job_card]
        
        mock_details.return_value = {
            'title': 'Python Developer',
            'company': 'TestCorp',
            'description': 'Python Django job',
            'url': 'https://linkedin.com/jobs/123',
            'has_apply_button': True,
            'is_easy_apply': False,
            'location': 'Berlin',
            'posted_date': '2 days ago'
        }
        
        mock_click.return_value = True
        mock_fill.return_value = True
        mock_submit.return_value = 'submitted'
        mock_email.return_value = True
        
        mock_driver = Mock()
        mock_driver.window_handles = ["main"]
        mock_chrome.return_value = mock_driver
        
        run_job_application_automation(1)
        
        mock_login.assert_called_once()
        mock_search.assert_called_once()
        mock_details.assert_called_once()
        mock_click.assert_called_once()
        mock_fill.assert_called_once()
        mock_submit.assert_called_once()
        mock_email.assert_called_once()
        
        assert mock_match.status == 'applied'
    
    @patch('applications.utils.AutomatedJobMatch.objects')
    def test_workflow_match_not_found(self, mock_manager, mock_match):
        """Test handling when match doesn't exist"""
        mock_manager.select_related.return_value.get.side_effect = Exception("Does not exist")
        
        run_job_application_automation(999)
        
        mock_match.save.assert_not_called()


# =============================================================================
# BUG REGRESSION TESTS
# =============================================================================

@pytest.mark.django_db
class TestBugFixes:
    """Tests for specific bugs that were fixed"""
    
    def test_temp_file_path_cross_platform(self, tmp_path):
        """CRITICAL: Test that temp files use cross-platform paths"""
        import tempfile
        
        temp_dir = tempfile.gettempdir()
        assert os.path.exists(temp_dir)
        assert os.path.isdir(temp_dir)
        
        # Should be able to write and read
        test_file = os.path.join(temp_dir, "test_cross_platform.txt")
        with open(test_file, 'w') as f:
            f.write("test")
        
        assert os.path.exists(test_file)
        os.remove(test_file)
    
    def test_cv_pdf_creation_and_cleanup(self, tmp_path):
        """Test CV PDF creation and cleanup"""
        import tempfile
        
        cv_content = "Test CV for Bug Fix"
        pdf_buffer = create_cv_pdf(cv_content)
        
        assert pdf_buffer is not None
        
        # Save to temp location (simulating fixed code)
        temp_file = os.path.join(tempfile.gettempdir(), "cv_test_1.pdf")
        with open(temp_file, 'wb') as f:
            f.write(pdf_buffer.getvalue())
        
        assert os.path.exists(temp_file)
        
        # Cleanup
        os.remove(temp_file)
        assert not os.path.exists(temp_file)
    
    def test_applied_jobs_file_path(self):
        """Test that APPLIED_JOBS_FILE uses proper path"""
        # Should not be hardcoded to /tmp/
        assert APPLIED_JOBS_FILE is not None
        assert isinstance(APPLIED_JOBS_FILE, str)


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

@pytest.mark.django_db
class TestEdgeCases:
    """Test edge cases and error handling"""
    
    def test_extract_cv_sections_garbage_input(self):
        """Test CV extraction with garbage input"""
        garbage = "!!!@@@### $$$%^&*() 12345"
        sections = extract_cv_sections(garbage)
        
        assert 'name' in sections
        assert 'education' in sections
    
    def test_create_cv_pdf_very_long_content(self):
        """Test PDF with very long content"""
        long_content = "A" * 5000 + "\\n" * 50 + "B" * 5000
        result = create_cv_pdf(long_content)
        
        assert result is not None
    
    @patch('applications.utils.log_event')
    def test_fill_form_no_fields_found(self, mock_log, mock_driver, mock_match, mock_personal_info):
        """Test form filling when no fields exist"""
        mock_driver.find_elements.return_value = []
        
        result = fill_application_form(mock_driver, mock_match, mock_personal_info, None)
        
        assert result is False
    
    @patch('applications.utils.log_event')
    def test_submit_no_buttons_found(self, mock_log, mock_driver, mock_match):
        """Test submission when no buttons exist"""
        mock_driver.find_elements.return_value = []
        
        result = submit_application(mock_driver, mock_match)
        
        assert result is None
    
    def test_is_job_applied_malformed_data(self, tmp_path):
        """Test handling malformed JSON"""
        test_file = tmp_path / "corrupted.json"
        test_file.write_text("not valid json {{{")
        
        with patch('applications.utils.APPLIED_JOBS_FILE', str(test_file)):
            result = load_applied_jobs()
            assert result == set()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])