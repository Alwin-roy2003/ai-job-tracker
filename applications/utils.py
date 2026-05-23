import os
import re
import time
import json
import imaplib
import email
import smtplib
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from dotenv import load_dotenv
from django.utils import timezone
from .models import AutomatedJobMatch, Job, ApplicationLog, PlatformConfig

from PyPDF2 import PdfReader
try:
    from PyPDF2 import PdfWriter
    PDFWRITER_AVAILABLE = True
except ImportError:
    from PyPDF2 import PdfMerger
    PDFWRITER_AVAILABLE = False

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

try:
    from .services.pdf_service import render_cv_pdf, render_cover_letter_pdf
    PDF_SERVICE_AVAILABLE = True
except ImportError:
    PDF_SERVICE_AVAILABLE = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException, NoSuchElementException,
        ElementClickInterceptedException, StaleElementReferenceException,
        ElementNotInteractableException, InvalidSessionIdException
    )
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError as e:
    SELENIUM_AVAILABLE = False
    print(f"[WARNING] Selenium not available: {e}")

# ── LLM provider — Google Gemini ──────────────────────────────────────────────
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

load_dotenv()

APPLIED_JOBS_FILE = os.path.join(tempfile.gettempdir(), "applied_jobs.json")


# =============================================================================
# APPLICANT DEFAULTS — reads personal info from .env (never hardcoded)
# =============================================================================

def _applicant_defaults():
    """Return a dict of personal info from environment variables.
    Generic placeholders are used if a var is missing so the code
    never crashes — but the real values MUST live in .env."""
    name = os.getenv('APPLICANT_NAME', 'Your Name')
    parts = name.split()
    return {
        'name':           name,
        'first_name':     os.getenv('APPLICANT_FIRST_NAME', parts[0] if parts else 'Your'),
        'last_name':      os.getenv('APPLICANT_LAST_NAME', parts[-1] if len(parts) > 1 else 'Name'),
        'email':          os.getenv('APPLICANT_EMAIL', 'your.email@example.com'),
        'phone':          os.getenv('APPLICANT_PHONE', '+490000000000'),
        'linkedin':       os.getenv('APPLICANT_LINKEDIN', 'https://linkedin.com/in/your-profile'),
        'location':       os.getenv('APPLICANT_LOCATION', 'Berlin, Germany'),
        'street_address': os.getenv('APPLICANT_STREET', ''),
        'postal_code':    os.getenv('APPLICANT_POSTAL', ''),
        'city':           os.getenv('APPLICANT_CITY', 'Berlin'),
        'country':        os.getenv('APPLICANT_COUNTRY', 'Germany'),
    }


# =============================================================================
# CONFIGURATION
# =============================================================================

class JobApplicationConfig:
    def __init__(self):
        self.job_role = ""
        self.platform = "linkedin"
        self.language = "english"
        self.location = ""
        self.keywords = []
        self.exclude_easy_apply = True
        self.last_24_hours = True
        self.max_applications = 10
        self.user_cv_path = None


# =============================================================================
# BROWSER SETUP
# =============================================================================

def create_chrome_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-breakpad")
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--disable-crash-reporter")
    options.add_argument("--disable-hang-monitor")
    options.add_argument("--disable-prompt-on-repost")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--no-first-run")
    options.add_argument("--safebrowsing-disable-auto-update")
    options.add_argument("--renderer-process-limit=2")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument("--lang=en-US")
    options.add_argument("--accept-lang=en-US,en")

    # NOTE: image-disabling removed — strong bot-detection signal on LinkedIn
    prefs = {
        "translate_whitelists": {"de": "en"},
        "translate": {"enabled": True},
        "intl.accept_languages": "en-US,en",
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    try:
        service = Service(ChromeDriverManager().install())
    except Exception as e:
        print(f"[WARNING] ChromeDriverManager failed ({e}), using system chromedriver")
        service = Service()

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": (
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en','de']});"
            "window.chrome={runtime:{}};"
        )
    })
    return driver


def enable_translation_on_page(driver, match=None):
    try:
        html_lang = driver.find_element(By.TAG_NAME, "html").get_attribute("lang") or ""
        if "de" not in html_lang.lower():
            return
        time.sleep(2)
        for sel in [
            "//button[contains(text(),'Translate')]",
            "//button[contains(text(),'Uebersetzen')]",
            "//*[@id='translateButton']",
        ]:
            try:
                btn = driver.find_element(By.XPATH, sel)
                if btn.is_displayed():
                    btn.click()
                    time.sleep(3)
                    return
            except Exception:
                continue
    except Exception:
        pass


# =============================================================================
# UTILITIES
# =============================================================================

def load_applied_jobs():
    if os.path.exists(APPLIED_JOBS_FILE):
        try:
            with open(APPLIED_JOBS_FILE, 'r') as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_applied_jobs(applied_set):
    try:
        with open(APPLIED_JOBS_FILE, 'w') as f:
            json.dump(list(applied_set), f, indent=2)
    except Exception as e:
        print(f"Error saving applied jobs: {e}")


def is_job_applied(job_url, company_name, job_title):
    applied = load_applied_jobs()
    if job_url and job_url in applied:
        return True
    if company_name and job_title:
        return f"{company_name.lower().strip()}_{job_title.lower().strip()}" in applied
    return False


def mark_job_applied(job_url, company_name, job_title):
    applied = load_applied_jobs()
    if job_url:
        applied.add(job_url)
    if company_name and job_title:
        applied.add(f"{company_name.lower().strip()}_{job_title.lower().strip()}")
    save_applied_jobs(applied)


def log_event(match, action, description):
    try:
        ApplicationLog.objects.create(match=match, action=action, description=description)
    except Exception:
        pass
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{action.upper()}] {description}")


def extract_keywords_from_job_description(description):
    if not description:
        return []
    skill_patterns = [
        r'python', r'django', r'flask', r'javascript', r'react', r'node\.?js',
        r'html', r'css', r'bootstrap', r'postgresql', r'mongodb', r'sql',
        r'rest', r'api', r'git', r'github', r'docker', r'aws', r'azure',
        r'linux', r'agile', r'scrum', r'jira', r'ci/cd', r'jenkins',
        r'machine learning', r'ai', r'data science', r'analytics',
        r'frontend', r'backend', r'full.?stack', r'web development',
        r'software engineer', r'developer', r'kubernetes', r'fastapi',
        r'redis', r'celery', r'nginx', r'elastic', r'kafka',
        r'security', r'owasp', r'rbac', r'authentication', r'authorization',
        r'typescript', r'java', r'spring', r'microservices', r'graphql',
    ]
    desc_lower = description.lower()
    found = []
    for pattern in skill_patterns:
        if re.search(pattern, desc_lower):
            clean = re.sub(r'[\\.*?+()\[\]{}^$|]', '', pattern).replace('  ', ' ').strip()
            if clean:
                found.append(clean)
    return list(set(found))


def normalize_text(text):
    if not text:
        return ""
    return ' '.join(str(text).lower().strip().split())


def extract_local_number(full_number, country_code="+49"):
    local = full_number.replace(country_code, "").replace("00" + country_code[1:], "")
    local = re.sub(r'\D', '', local)
    return local.lstrip('0')


# =============================================================================
# ATS SCORE
# =============================================================================

def calculate_ats_score(job_description, job_title, matched_keywords, all_keywords):
    if not job_description:
        return 85
    desc_lower = job_description.lower()
    title_lower = job_title.lower()
    title_score = min(25, sum(8 for kw in matched_keywords if kw.lower() in title_lower))
    total_kw = len(all_keywords) if all_keywords else 10
    density_score = (len(matched_keywords) / max(total_kw, 5)) * 35
    skills_score = 15
    try:
        for pattern in [
            r'(?:skills|requirements|qualifications)[\:\s]*(.{0,800})',
            r'(?:technical skills|key requirements)[\:\s]*(.{0,800})',
        ]:
            m = re.search(pattern, desc_lower)
            if m:
                sec = m.group(1)
                cnt = sum(1 for kw in matched_keywords if kw.lower() in sec)
                skills_score = min(25, (cnt / max(len(matched_keywords), 1)) * 25 + 10)
                break
    except Exception:
        pass
    exp_score = 0
    if any(w in desc_lower for w in ['experience', 'year', 'years']):
        exp_score += 8
    if any(w in desc_lower for w in ['bachelor', 'master', 'degree', 'b.sc', 'm.sc']):
        exp_score += 7
    return min(98, max(88, int(title_score + density_score + skills_score + exp_score)))


# =============================================================================
# COOKIE HANDLER
# =============================================================================

def handle_cookie_popup(driver, match=None):
    TEXT_PATTERNS = [
        'accept all', 'accept all cookies', 'accept cookies', 'allow all',
        'allow all cookies', 'i agree', 'i accept', 'agree and continue',
        'agree & continue', 'ok', 'okay', 'got it', 'continue',
        'akzeptieren', 'alle akzeptieren', 'alle cookies akzeptieren',
        'zustimmen', 'einverstanden', 'ok und weiter', 'weiter',
        'ich stimme zu', 'cookies akzeptieren', 'annehmen',
    ]

    def _try_click(btn):
        try:
            if not btn.is_displayed():
                return False
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            try:
                btn.click()
            except (ElementClickInterceptedException, ElementNotInteractableException):
                driver.execute_script("arguments[0].click();", btn)
            time.sleep(1.2)
            return True
        except Exception:
            return False

    UP = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    LO = 'abcdefghijklmnopqrstuvwxyz'

    for text in TEXT_PATTERNS:
        try:
            xpath = (
                f"//button[translate(normalize-space(.),"
                f"'{UP}','{LO}')='{text}']"
                f"|//a[translate(normalize-space(.),"
                f"'{UP}','{LO}')='{text}']"
                f"|//span[translate(normalize-space(.),"
                f"'{UP}','{LO}')='{text}']"
            )
            for btn in driver.find_elements(By.XPATH, xpath):
                if _try_click(btn):
                    return True
        except Exception:
            continue

    CONTAINS = ['accept', 'allow', 'agree', 'consent', 'got it',
                'akzeptier', 'zustimm', 'einverstand', 'annehm']
    for kw in CONTAINS:
        try:
            for btn in driver.find_elements(
                    By.XPATH,
                    f"//button[contains(translate(normalize-space(.),"
                    f"'{UP}','{LO}'),'{kw}')]"):
                txt = (btn.text or '').lower()
                if any(bad in txt for bad in ['reject', 'decline', 'necessary only',
                                               'ablehnen', 'nur notwendige']):
                    continue
                if _try_click(btn):
                    return True
        except Exception:
            continue

    CSS_SELECTORS = [
        "button[id*='accept']", "button[id*='agree']", "button[id*='consent']",
        "button[class*='accept']", "button[class*='agree']", "button[class*='consent']",
        "button[id*='cookie']", "button[class*='cookie']",
        "#onetrust-accept-btn-handler",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#didomi-notice-agree-button",
        ".cc-accept", ".cc-btn.cc-allow",
        "[data-testid='cookie-accept']",
        "[aria-label*='accept']", "[aria-label*='Accept']",
    ]
    for sel in CSS_SELECTORS:
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                if _try_click(btn):
                    return True
        except Exception:
            continue
    return False


# =============================================================================
# EMAIL VERIFICATION
# =============================================================================

def check_email_for_verification(match, max_wait=300):
    try:
        email_user = os.getenv("EMAIL_HOST_USER")
        email_pass = os.getenv("EMAIL_HOST_PASSWORD")
        if not email_user or not email_pass:
            log_event(match, "warning", "Email credentials not configured")
            return None
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_user, email_pass)
        mail.select('inbox')
        since = (datetime.now() - timedelta(minutes=10)).strftime("%d-%b-%Y")
        start = time.time()
        link = None
        while time.time() - start < max_wait:
            try:
                mail.select('inbox')
                _, data = mail.search(None, f'(SINCE "{since}" UNSEEN)')
                for eid in reversed(data[0].split()[-5:]):
                    _, msg_data = mail.fetch(eid, '(RFC822)')
                    msg = email.message_from_bytes(msg_data[0][1])
                    subj = msg.get('Subject', '').lower()
                    if not any(k in subj for k in ['verify', 'confirm', 'activation', 'welcome']):
                        continue
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() in ("text/plain", "text/html"):
                                body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                break
                    else:
                        body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                    for pat in [
                        r'https?://[^\s<>"]+?(?:verify|confirm|activate|auth)[^\s<>"]*',
                        r'https?://[^\s<>"]+?(?:token|code|key)=[^\s<>"]*',
                    ]:
                        found = re.findall(pat, body, re.IGNORECASE)
                        if found:
                            link = found[0]
                            mail.store(eid, '+FLAGS', '\\Seen')
                            break
                    if link:
                        break
                if link:
                    break
                time.sleep(10)
            except Exception as e:
                log_event(match, "warning", f"Email check error: {e}")
                time.sleep(10)
        mail.logout()
        return link
    except Exception as e:
        log_event(match, "error", f"Email verification check failed: {e}")
        return None


def handle_email_verification(driver, match, verification_link):
    try:
        if not verification_link:
            return False
        driver.execute_script(f"window.open('{verification_link}', '_blank');")
        time.sleep(3)
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(5)
        handle_cookie_popup(driver, match)
        indicators = ['success', 'verified', 'confirmed', 'welcome', 'thank you']
        if any(i in driver.current_url.lower() for i in indicators):
            return True
        try:
            if any(i in driver.find_element(By.TAG_NAME, "body").text.lower()
                   for i in indicators):
                return True
        except Exception:
            pass
        return True
    except Exception as e:
        log_event(match, "error", f"Email verification error: {e}")
        return False


# =============================================================================
# CV SECTIONS
# =============================================================================

def extract_cv_sections(cv_text):
    sections = {
        'professional_summary': (
            'Computer Science (BSc) student at Berlin School of Business and Innovation '
            'with hands-on professional experience in Python, JavaScript, RESTful API '
            'integration, and SQL-based data workflows. Built full-stack internal tools '
            'that automate manual processes, integrate third-party SaaS APIs, and '
            'transform structured data. Comfortable with PostgreSQL, MySQL, scripting, '
            'low-code automation (n8n, Zapier), and CI/CD with GitHub Actions. '
            'Authorised to work in Germany up to 20 hrs/week; available immediately.'
        ),
        'skills': {
            'Automation & Scripting': (
                'Python scripts for data processing, backend workflows and SaaS task '
                'automation; eliminating repetitive manual work'
            ),
            'APIs & Integrations': (
                'Designing and consuming RESTful APIs in Django REST Framework; '
                'JSON-based data exchange between SaaS tools and internal systems'
            ),
            'SQL & Data': (
                'Writing SQL queries for PostgreSQL, MySQL, MongoDB; data modelling, '
                'parsing, mapping and transformation between sources'
            ),
            'Internal Tools & Full-Stack': (
                'Django, Flask, HTML5, CSS3, JavaScript, Bootstrap \u2014 building '
                'internal products and workflows that solve real business problems'
            ),
            'Low-Code, CI/CD & AI Tooling': (
                'Academic and self-driven exposure to n8n, Zapier, GitHub Actions; '
                'daily use of Claude Code and Google Antigravity IDE'
            ),
            'Communication & Documentation': (
                'Clear technical documentation; comfortable partnering with '
                'non-technical stakeholders to translate needs into solutions'
            ),
            'Languages': 'English C1 (Advanced); German A1 (basic, actively improving)',
        },
        'education': [
            {
                'degree': 'BSc (Hons) Computer Science and Digitisation',
                'institution': 'Berlin School of Business and Innovation',
                'location': 'Berlin, Germany',
                'dates': 'October 2024 \u2013 July 2027 (expected)',
            },
            {
                'degree': 'Diploma in Computer Engineering',
                'institution': "St Mary's Polytechnic College",
                'location': 'Kerala, India',
                'dates': 'June 2021 \u2013 May 2024',
            },
        ],
        'experience': [
            {
                'title': 'Junior Python Full Stack Developer',
                'company': 'Soften Technologies',
                'location': 'Kerala, India',
                'dates': 'December 2023 \u2013 May 2024',
                'bullets': [
                    'Built RESTful APIs using Django REST Framework with JSON data '
                    'exchange between frontend and backend systems',
                    'Wrote Python scripts to automate data processing and backend '
                    'workflows, removing repetitive manual tasks from the team',
                    'Designed data transformation logic to parse, map and normalise '
                    'structured data; persisted records to PostgreSQL and MySQL',
                    'Delivered full-stack internal web applications (Django, Flask, '
                    'HTML5, CSS3, JavaScript, Bootstrap); used GitHub for version '
                    'control throughout',
                    'Documented APIs, data flows and setup instructions so '
                    'non-engineering stakeholders could reliably operate the tools',
                ],
            },
        ],
        'projects': [
            {
                'name': 'AI Job Hunter Tool',
                'technologies': 'Python, Django REST Framework, REST APIs, JSON, PostgreSQL, Selenium, JavaScript',
                'bullets': [
                    'Built a RESTful backend that integrates third-party job APIs '
                    'through custom converters, normalising listings into PostgreSQL',
                    'Created an AI-powered recommendation engine in Python matching '
                    'user profiles to listings via structured data analysis',
                    'Automated end-to-end browser workflows with Selenium (login, '
                    'search, submission) using CSS/XPath selectors, retries and '
                    'error recovery',
                ],
            },
            {
                'name': 'AI Dental Receptionist (in development)',
                'technologies': 'Vapi, Claude API, Twilio, Supabase, Next.js, Tailwind',
                'bullets': [
                    'Building an AI voice agent for dental clinics that handles '
                    'after-hours calls, classifies urgency through LLM analysis and '
                    'routes cases to the doctor by alarm call or SMS \u2014 with '
                    'GDPR-compliant EU hosting and deletion flow',
                ],
            },
        ],
        'languages': 'English C1 (Advanced); German A1 (basic, actively improving)',
        'interests': [
            'Low-code automation and DevOps tooling (n8n, Zapier, GitHub Actions)',
            'AI-assisted development workflows and prompt engineering',
            'Internal tooling, SaaS integrations and business process automation',
            'Coding challenges, hackathons and developer communities',
        ],
    }

    if not cv_text:
        return sections

    lines = cv_text.split('\n')
    current_section = None
    raw = {k: [] for k in ['professional_summary', 'education', 'experience',
                             'projects', 'skills', 'languages', 'interests']}
    SECTION_MAP = {
        'PROFESSIONAL SUMMARY': 'professional_summary',
        'SUMMARY': 'professional_summary',
        'PROFILE': 'professional_summary',
        'TECHNICAL SKILLS': 'skills',
        'KEY SKILLS': 'skills',
        'SKILLS': 'skills',
        'EDUCATION': 'education',
        'PROFESSIONAL EXPERIENCE': 'experience',
        'WORK EXPERIENCE': 'experience',
        'EXPERIENCE': 'experience',
        'PROJECTS': 'projects',
        'LANGUAGES': 'languages',
        'LANGUAGES AND AVAILABILITY': 'languages',
        'INTERESTS': 'interests',
    }

    # Detect ALL-CAPS lines that look like section headers but aren't in our map.
    # These should still reset current_section to None so we don't pollute the
    # previous section with foreign content.
    def _looks_like_unknown_header(line_upper, line_stripped):
        if len(line_stripped) > 35:
            return False
        if line_upper != line_stripped:  # must be all-caps
            return False
        if not re.match(r'^[A-Z][A-Z\s&/\-]+$', line_stripped):
            return False
        return True

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        matched = False
        for key, sec in SECTION_MAP.items():
            if upper == key or upper.startswith(key + ' '):
                current_section = sec
                matched = True
                break
        if not matched:
            if _looks_like_unknown_header(upper, stripped):
                # Unknown all-caps header — stop dumping into prev section
                current_section = None
                continue
            if current_section and stripped:
                raw[current_section].append(stripped)

    # Sanity checks: only override defaults if parsed content looks clean.
    # Anything way longer than expected = parser ate junk, keep default.
    if raw['professional_summary']:
        joined = ' '.join(raw['professional_summary'])
        if 50 < len(joined) < 1200:
            sections['professional_summary'] = joined
    if raw['languages']:
        joined = ' '.join(raw['languages'])
        if 5 < len(joined) < 200:
            sections['languages'] = joined
    if raw['interests']:
        cleaned = [l.lstrip('\u2022- ') for l in raw['interests']
                   if l.strip() and len(l) < 200]
        if cleaned and len(cleaned) <= 8:
            sections['interests'] = cleaned
    if raw['skills']:
        parsed = {}
        for line in raw['skills']:
            if ':' in line and len(line) < 300:
                cat, _, vals = line.partition(':')
                cat_clean = cat.strip()
                if 2 < len(cat_clean) < 40:
                    parsed[cat_clean] = vals.strip()
        if parsed and len(parsed) <= 10:
            sections['skills'] = parsed

    return sections


def _clean_role_title(raw_title):
    """Strip parens, gender markers and status markers from a job title so the
    CV header doesn't read like 'Mitarbeiter:in Ressourcenmonitoring – befristet (w/m/d)'.
    If the result is still too long or contains awkward German fragments,
    fall back to a generic, recruiter-friendly title."""
    if not raw_title:
        return "Software Developer"
    t = str(raw_title)
    # Remove parenthetical content e.g. (w/m/d), (f/m/x), (m/w/d), (Remote)
    t = re.sub(r'\([^)]*\)', '', t)
    # Remove inline gender-inclusive markers e.g. "Mitarbeiter:in", "Mitarbeiter*in", "Mitarbeiter/in"
    t = re.sub(r'(?<=\w)[:\*/](?:in|innen)\b', '', t, flags=re.IGNORECASE)
    # Remove status / contract markers after a dash
    t = re.sub(
        r'\s*[-\u2013\u2014]\s*(befristet|unbefristet|temporary|permanent|'
        r'teilzeit|vollzeit|part.?time|full.?time|remote|hybrid|onsite|on-site)\b.*$',
        '', t, flags=re.IGNORECASE
    )
    # Remove trailing "m/f/x", "m/w/d" without parens
    t = re.sub(r'\bm/[fw]/[dx]\b', '', t, flags=re.IGNORECASE)
    # Collapse whitespace; strip trailing punctuation
    t = re.sub(r'\s+', ' ', t).strip(' -\u2013\u2014/:,.')
    if not t:
        return "Software Developer"

    # Heuristic fallback: if the title is still too long (>35 chars) or contains
    # obvious German role chunks recruiters in other countries won't recognise,
    # try to extract a short English-friendly version OR fall back to generic.
    GERMAN_FRAGMENTS = [
        'gewerblich', 'technische berufe', 'kaufm', 'fachkraft',
        'mitarbeiter', 'mitarbeit', 'sachbearbeit', 'auszubildende',
        'angestellte', 'beschaeftigt', 'beschäftigt',
    ]
    t_lower = t.lower()
    has_german = any(g in t_lower for g in GERMAN_FRAGMENTS)

    if len(t) <= 35 and not has_german:
        return t

    # Try to keep only the leading English-looking words
    words = t.split()
    short = []
    for w in words:
        if len(' '.join(short + [w])) > 30:
            break
        # Skip obviously non-English German fragments
        if any(g in w.lower() for g in GERMAN_FRAGMENTS):
            break
        short.append(w)
    cleaned = ' '.join(short).strip(' -\u2013\u2014/:,.')

    # If we got a reasonable short version, use it; otherwise generic fallback
    if cleaned and len(cleaned) >= 5:
        return cleaned
    return "Software Developer"


def extract_personal_info(cv_text, file_path=None):
    _defaults = _applicant_defaults()
    info = {
        'name':           _defaults['name'],
        'first_name':     _defaults['first_name'],
        'last_name':      _defaults['last_name'],
        'phone':          _defaults['phone'],
        'email':          _defaults['email'],
        'linkedin':       _defaults['linkedin'],
        'location':       _defaults['location'],
        'street_address': _defaults['street_address'],
        'postal_code':    _defaults['postal_code'],
        'city':           _defaults['city'],
        'country':        _defaults['country'],
        'visa_status': 'Student Visa',
        'availability': 'immediately; up to 20 hours per week',
        'languages': 'English: C1 Advanced (fluent); German: A1 (basic)',
        'notice_period': '1 month',
        'salary_expectation': 'Negotiable',
        'experience_years': '1',
        'degree': 'BSc (Hons) Computer Science and Digitisation',
        'tagline': '',
    }
    if not cv_text:
        return info
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', cv_text)
    if m:
        info['email'] = m.group(0)
    m = re.search(r'\+?\d{1,3}[\s-]?\d{3,4}[\s-]?\d{4,}', cv_text)
    if m:
        info['phone'] = m.group(0)
    m = re.search(r'linkedin\.com/in/[\w-]+', cv_text, re.IGNORECASE)
    if m:
        info['linkedin'] = f"https://{m.group(0)}"
    return info


def get_resume_text(file_path):
    text = ""
    try:
        if file_path and file_path.endswith('.pdf'):
            reader = PdfReader(file_path)
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except Exception as e:
        print(f"Error reading PDF: {e}")
    return text


# =============================================================================
# BUILD TAILORED CV
# =============================================================================

def build_tailored_cv(base_sections, job_title, company_name, job_description, job_keywords):
    desc_kw = extract_keywords_from_job_description(job_description)
    all_keywords = list(set(job_keywords + desc_kw))
    kw_str = ', '.join(all_keywords[:6]) if all_keywords else 'Python, Django, Flask, REST APIs, PostgreSQL'
    kw_count = len(all_keywords) if all_keywords else 8

    # Adaptive primary stack — don't claim Python if the job is JS-only, etc.
    kw_lower = {k.lower() for k in all_keywords}
    has_python = bool(kw_lower & {'python', 'django', 'flask', 'fastapi'})
    has_js     = bool(kw_lower & {'javascript', 'typescript', 'react', 'node',
                                  'node.js', 'vue', 'angular', 'next.js'})
    has_data   = bool(kw_lower & {'sql', 'postgresql', 'mysql', 'mongodb',
                                  'data', 'analytics', 'machine learning', 'ai'})

    if has_python and has_js:
        primary_stack = "full-stack web development with Python (Django/Flask) and JavaScript"
    elif has_python:
        primary_stack = "Python full-stack web development with Django and Flask"
    elif has_js:
        primary_stack = "JavaScript/TypeScript full-stack development with React and Node.js"
    elif has_data:
        primary_stack = "data-driven backend development with SQL and Python"
    else:
        primary_stack = "full-stack web development across modern frameworks"

    clean_title = _clean_role_title(job_title)

    summary = (
        f"Computer Science student based in Berlin with 1+ year of hands-on experience "
        f"in {primary_stack}, having delivered 5+ production applications using "
        f"{kw_str}. Reduced average API response times by 60% through query optimisation "
        f"and shipped RBAC systems serving multiple user roles across two release cycles. "
        f"Seeking the {clean_title} role at {company_name} to apply backend engineering "
        f"expertise and contribute to scalable software delivery. Authorised to work in "
        f"Germany on a student visa, available immediately for up to 20 hours per week."
    )

    base_skills = base_sections.get('skills', {})
    if not isinstance(base_skills, dict):
        base_skills = {}

    lang_add = [k for k in all_keywords if k in ['python','javascript','typescript','java','go','rust','html','css']]
    fw_add   = [k for k in all_keywords if k in ['django','flask','react','fastapi','node','spring','express','vue','angular']]
    db_add   = [k for k in all_keywords if k in ['postgresql','mongodb','mysql','redis','elasticsearch','kafka','sql']]
    ops_add  = [k for k in all_keywords if k in ['docker','kubernetes','aws','azure','ci/cd','jenkins','nginx','linux','git','github']]

    def _merge(base_str, additions):
        existing = {x.strip().lower() for x in base_str.split(',')}
        for a in additions:
            if a.lower() not in existing:
                base_str += f', {a.capitalize()}'
                existing.add(a.lower())
        return base_str

    tailored_skills = dict(base_skills) if base_skills else {
        'Python & Django': 'Full-stack web development using Django, Django REST Framework and Flask with secure, scalable backend architecture',
        'Database Management': 'PostgreSQL, MongoDB, MySQL \u2014 schema design, query optimisation and secure data handling',
        'API Development': 'RESTful API design and integration, JSON, third-party API connectivity and input validation',
        'Frontend Technologies': 'HTML5, CSS3, JavaScript, Bootstrap \u2014 responsive and user-friendly interface development',
        'Security & DevOps': 'OWASP Top 10, RBAC, authentication and authorisation, CI/CD fundamentals, Docker and basic cloud concepts',
        'Development Tools': 'Git, GitHub, VS Code, PyCharm \u2014 version control, code review and Agile workflows',
        'Problem Solving': 'Debugging, software testing, SDLC understanding, technical communication and collaborative delivery',
    }

    if lang_add or fw_add:
        tailored_skills['Python & Django'] = _merge(tailored_skills.get('Python & Django', ''), lang_add + fw_add)
    if db_add:
        tailored_skills['Database Management'] = _merge(tailored_skills.get('Database Management', ''), db_add)
    if ops_add:
        tailored_skills['Security & DevOps'] = _merge(tailored_skills.get('Security & DevOps', ''), ops_add)

    base_exp = base_sections.get('experience', [])
    if not isinstance(base_exp, list):
        base_exp = []
    tailored_exp = []
    for exp in base_exp:
        new_exp = dict(exp)
        bullets = list(exp.get('bullets', []))
        if all_keywords:
            tech_top = [k for k in all_keywords
                        if k.lower() not in {'rest','api','sql','ai','ci/cd'}][:3]
            if not tech_top:
                tech_top = all_keywords[:3]
            tech_str = ', '.join(
                k.upper() if len(k) <= 3 else k.title() for k in tech_top
            )
            bullets.append(
                f"Delivered production-ready features in {tech_str}, contributing "
                f"to scalable backend services and end-to-end integration workflows "
                f"that aligned with the team's quality and performance targets"
            )
        new_exp['bullets'] = bullets
        tailored_exp.append(new_exp)
    if not tailored_exp:
        tailored_exp = [{
            'title': 'Junior Python Full Stack Developer',
            'company': 'Soften Technologies',
            'location': 'Kerala, India',
            'dates': 'December 2023 \u2013 May 2024',
            'bullets': [
                'Developed 5+ Python full-stack web applications using Django and Flask',
                'Designed 20+ RESTful API endpoints with authentication and input validation',
                'Implemented RBAC across 4 user roles, eliminating unauthorised access incidents',
                'Optimised PostgreSQL queries, cutting average API response time by 60%',
                f'Delivered features in {kw_str}, contributing to scalable backend services',
            ],
        }]

    sections = {
        'professional_summary': summary,
        'skills': tailored_skills,
        'education': base_sections.get('education', []),
        'experience': tailored_exp,
        'projects': base_sections.get('projects', []),
        'languages': base_sections.get('languages', 'English: C1 Advanced (fluent); German: A1 (basic)'),
        'interests': base_sections.get('interests', []),
    }
    return sections, all_keywords


# =============================================================================
# PDF SHARED HELPERS
# =============================================================================

def _wrap_text(c, text, x, y, max_w, font, size, lh, page_h, margin=50):
    c.setFont(font, size)
    words = text.split()
    line = ''
    for word in words:
        test = (line + ' ' + word).strip()
        if c.stringWidth(test, font, size) <= max_w:
            line = test
        else:
            if line:
                c.drawString(x, y, line)
                y -= lh
                if y < margin:
                    c.showPage()
                    y = page_h - 15 * mm
                    c.setFont(font, size)
            line = word
    if line:
        c.drawString(x, y, line)
        y -= lh
    return y


def _section_header(c, title, x, y, width, page_h):
    if y < 90:
        c.showPage()
        y = page_h - 14 * mm
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(x, y, title.upper())
    y -= 4
    c.setLineWidth(0.8)
    c.line(x, y, x + width, y)
    y -= 10
    return y


def _draw_skill_bullet(c, label, description, x, y, tw, lh, page_h):
    FONT_SIZE  = 10
    INDENT_X   = x + 5 * mm
    bullet_str = "\u2022 "
    label_str  = f"{label}: "
    bw = c.stringWidth(bullet_str, "Helvetica",      FONT_SIZE)
    lw = c.stringWidth(label_str,  "Helvetica-Bold", FONT_SIZE)

    c.setFont("Helvetica", FONT_SIZE)
    c.drawString(x, y, bullet_str)
    c.setFont("Helvetica-Bold", FONT_SIZE)
    c.drawString(x + bw, y, label_str)
    c.setFont("Helvetica", FONT_SIZE)

    first_avail = tw - bw - lw
    cont_avail  = tw - (INDENT_X - x)
    words      = description.split()
    line       = ''
    first_line = True

    for word in words:
        test  = (line + ' ' + word).strip()
        avail = first_avail if first_line else cont_avail
        if c.stringWidth(test, "Helvetica", FONT_SIZE) <= avail:
            line = test
        else:
            if line:
                draw_x = (x + bw + lw) if first_line else INDENT_X
                c.drawString(draw_x, y, line)
                y -= lh
                if y < 60:
                    c.showPage()
                    y = page_h - 14 * mm
                    c.setFont("Helvetica", FONT_SIZE)
            line       = word
            first_line = False

    if line:
        draw_x = (x + bw + lw) if first_line else INDENT_X
        c.drawString(draw_x, y, line)
        y -= lh
    return y


# =============================================================================
# CV PDF
# =============================================================================

def create_cv_pdf(cv_content, personal_info=None):
    try:
        buf  = BytesIO()
        c    = canvas.Canvas(buf, pagesize=A4)
        W, H = A4
        L    = 15 * mm
        R    = W - 15 * mm
        TW   = R - L
        y    = H - 14 * mm
        LH   = 13
        LH_S = 12

        pi = personal_info or {}
        _defs    = _applicant_defaults()
        name     = pi.get('name',     _defs['name']).upper()
        phone    = pi.get('phone',    _defs['phone'])
        em       = pi.get('email',    _defs['email'])
        loc      = pi.get('location', _defs['location'])
        linkedin = (pi.get('linkedin', _defs['linkedin'])
                    .replace('https://', '').replace('http://', ''))

        skills = cv_content.get('skills', {}) if isinstance(cv_content, dict) else {}

        tagline = pi.get('tagline', '')
        if not tagline:
            if isinstance(skills, dict) and skills:
                tagline = ' | '.join(list(skills.keys())[:4])
            else:
                tagline = 'Python Full Stack Developer | Django & REST APIs | PostgreSQL | Berlin'

        while c.stringWidth(tagline, "Helvetica", 9) > TW and ' | ' in tagline:
            tagline = tagline.rsplit(' | ', 1)[0]

        # Header
        c.setFont("Helvetica-Bold", 17)
        c.drawCentredString(W / 2, y, name)
        y -= 16

        c.setFont("Helvetica", 9)
        c.drawCentredString(W / 2, y, tagline)
        y -= 12

        contact_line = f"{phone}, {em}, {linkedin}, {loc}"
        fs = 8.5
        while c.stringWidth(contact_line, "Helvetica", fs) > TW and fs > 7.0:
            fs -= 0.25
        c.setFont("Helvetica", fs)
        c.drawCentredString(W / 2, y, contact_line)
        y -= 7

        c.setLineWidth(0.9)
        c.line(L, y, R, y)
        y -= 13

        # Professional Summary
        summary = (cv_content.get('professional_summary', '')
                   if isinstance(cv_content, dict) else str(cv_content))
        if summary:
            y = _section_header(c, "Professional Summary", L, y, TW, H)
            y = _wrap_text(c, summary, L, y, TW, "Helvetica", 10, LH, H)
            y -= 5

        # Key Skills
        if isinstance(skills, dict) and skills:
            y = _section_header(c, "Key Skills", L, y, TW, H)
            for label, description in skills.items():
                if y < 80:
                    c.showPage(); y = H - 14 * mm
                y = _draw_skill_bullet(c, label, description, L, y, TW, LH_S, H)
            y -= 4

        # Experience
        experience = cv_content.get('experience', []) if isinstance(cv_content, dict) else []
        if experience:
            y = _section_header(c, "Professional Experience", L, y, TW, H)
            for exp in experience:
                if y < 80:
                    c.showPage(); y = H - 14 * mm
                c.setFont("Helvetica-Bold", 10.5)
                c.drawString(L, y, exp.get('title', ''))
                y -= LH
                c.setFont("Helvetica", 9.5)
                c.drawString(L, y, f"{exp.get('company', '')}, {exp.get('location', '')}")
                c.drawRightString(R, y, exp.get('dates', ''))
                y -= LH
                for bullet in exp.get('bullets', []):
                    if y < 70:
                        c.showPage(); y = H - 14 * mm
                        c.setFont("Helvetica", 10)
                    y = _wrap_text(c, f"\u2022 {bullet}", L + 3 * mm, y,
                                   TW - 3 * mm, "Helvetica", 10, LH, H)
                y -= 4
            y -= 2

        # Projects
        projects = cv_content.get('projects', []) if isinstance(cv_content, dict) else []
        if projects:
            y = _section_header(c, "Projects", L, y, TW, H)
            for proj in projects:
                if y < 90:
                    c.showPage(); y = H - 14 * mm
                # Project name in bold
                c.setFont("Helvetica-Bold", 10.5)
                c.drawString(L, y, proj.get('name', ''))
                # Technologies on same line, right-aligned in italic
                tech = proj.get('technologies', '')
                if tech:
                    c.setFont("Helvetica-Oblique", 9)
                    # If tech string is too long, drop to next line
                    name_w = c.stringWidth(proj.get('name', ''), "Helvetica-Bold", 10.5)
                    tech_w = c.stringWidth(tech, "Helvetica-Oblique", 9)
                    if name_w + tech_w + 20 <= TW:
                        c.drawRightString(R, y, tech)
                        y -= LH
                    else:
                        y -= LH
                        c.setFont("Helvetica-Oblique", 9)
                        y = _wrap_text(c, tech, L, y, TW,
                                       "Helvetica-Oblique", 9, LH_S, H)
                else:
                    y -= LH
                # Bullets
                for bullet in proj.get('bullets', []):
                    if y < 70:
                        c.showPage(); y = H - 14 * mm
                        c.setFont("Helvetica", 10)
                    y = _wrap_text(c, f"\u2022 {bullet}", L + 3 * mm, y,
                                   TW - 3 * mm, "Helvetica", 10, LH, H)
                y -= 4
            y -= 2

        # Education
        education = cv_content.get('education', []) if isinstance(cv_content, dict) else []
        if education:
            y = _section_header(c, "Education", L, y, TW, H)
            for edu in education:
                if y < 80:
                    c.showPage(); y = H - 14 * mm
                c.setFont("Helvetica-Bold", 10.5)
                c.drawString(L, y, edu.get('degree', ''))
                y -= LH
                c.setFont("Helvetica", 9.5)
                c.drawString(L, y, f"{edu.get('institution', '')}, {edu.get('location', '')}")
                c.drawRightString(R, y, edu.get('dates', ''))
                y -= LH + 5
            y -= 2

        # Languages and Availability — hardened against garbled parser output
        y = _section_header(c, "Languages and Availability", L, y, TW, H)
        langs = ''
        if isinstance(cv_content, dict):
            langs = cv_content.get('languages', '')
        # Defensive cap — if parser dumped junk in here, fall back to default
        if not langs or len(langs) > 200:
            langs = 'English C1 (Advanced); German A1 (basic, actively improving)'
        avail = pi.get('availability', 'immediately; up to 20 hours per week')
        visa  = pi.get('visa_status',  'Student Visa')

        c.setFont("Helvetica", 10)
        y = _wrap_text(c, f"Languages \u2014 {langs}", L, y, TW, "Helvetica", 10, LH, H)
        y = _wrap_text(c,
            f"Availability \u2014 Available {avail}; "
            f"fully authorised to work in Germany ({visa})",
            L, y, TW, "Helvetica", 10, LH, H)

        c.save()
        buf.seek(0)
        return buf

    except Exception as e:
        print(f"[ERROR] create_cv_pdf: {e}")
        import traceback; traceback.print_exc()
        return None


# =============================================================================
# COVER LETTER PDF
# =============================================================================

def create_cover_letter_pdf(content, personal_info=None, company_name=None, job_title=None):
    try:
        buf  = BytesIO()
        c    = canvas.Canvas(buf, pagesize=A4)
        W, H = A4
        L    = 20 * mm
        R    = W - 20 * mm
        TW   = R - L
        y    = H - 18 * mm
        LH   = 14.5

        pi    = personal_info or {}
        _defs = _applicant_defaults()
        name  = pi.get('name',     _defs['name']).upper()
        phone = pi.get('phone',    _defs['phone'])
        em    = pi.get('email',    _defs['email'])
        loc   = pi.get('location', _defs['location'])

        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(W / 2, y, name)
        y -= 14

        c.setFont("Helvetica", 9)
        c.drawCentredString(W / 2, y, f"{phone} \u00b7 {em} \u00b7 {loc}")
        y -= 8

        c.setLineWidth(0.8)
        c.line(L, y, R, y)
        y -= 20

        all_lines = content.split('\n')
        HEADER_PATTERNS = [
            r'^[A-Z][A-Z\s]+$',
            r'[\w\.\-]+@[\w\.\-]+\.\w+',
            r'^\+?\d[\d\s\-]{6,}',
            r'^linkedin\.com',
            r'^https?://',
        ]

        body_start = 0
        for i, raw_line in enumerate(all_lines):
            stripped = raw_line.strip()
            if not stripped:
                if i < 6:
                    continue
                body_start = i
                break
            is_hdr = any(re.search(p, stripped, re.IGNORECASE) for p in HEADER_PATTERNS)
            if not is_hdr:
                body_start = i
                break

        for line in all_lines[body_start:]:
            line = line.rstrip()
            if not line:
                y -= LH * 0.55
                continue
            if y < 55:
                c.showPage()
                y = H - 18 * mm
            c.setFont("Helvetica", 10.5)
            y = _wrap_text(c, line, L, y, TW, "Helvetica", 10.5, LH, H)

        c.save()
        buf.seek(0)
        return buf

    except Exception as e:
        print(f"[ERROR] create_cover_letter_pdf: {e}")
        return None


# =============================================================================
# COVER LETTER TEXT — Gemini primary, template fallback
# =============================================================================

def generate_tailored_cover_letter(personal_info, job_title, company_name,
                                   job_description, keywords):
    _defs    = _applicant_defaults()
    name     = personal_info.get('name',     _defs['name'])
    em       = personal_info.get('email',    _defs['email'])
    phone    = personal_info.get('phone',    _defs['phone'])
    location = personal_info.get('location', _defs['location'])
    linkedin_short = personal_info.get('linkedin', _defs['linkedin']).replace('https://', '').replace('http://', '')
    date_str = datetime.now().strftime('%d %B %Y')

    company_name = (company_name or '').strip()
    if not company_name or company_name.lower() in ('unknown company', 'unknown', 'company', 'n/a', ''):
        company_name = 'your organisation'

    job_title = (job_title or '').strip()
    if not job_title or job_title.lower() in ('unknown position', 'unknown', 'position', ''):
        job_title = 'the advertised position'

    kw_str = ', '.join(keywords[:6]) if keywords else 'Python, Django, REST APIs'

    header = (
        f"{name}\n"
        f"{em}  |  {phone}\n"
        f"{location}\n"
        f"{linkedin_short}\n\n"
    )

    clean_title = job_title
    kw_top = keywords[:4] if keywords else ['Python', 'Django', 'REST APIs', 'PostgreSQL']
    kw_top_str = ', '.join(kw_top)

    # ── Gemini API (primary) ──────────────────────────────────────────────────
    if GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY"):
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

            prompt = (
                f"Write a professional cover letter following the EXACT 4-paragraph template below. "
                f"Do not add extra paragraphs, headers, or formatting.\n\n"
                f"=== EXACT TEMPLATE TO FOLLOW ===\n\n"
                f"{date_str}\n"
                f"\n"
                f"{company_name}\n"
                f"\n"
                f"Dear Hiring Team,\n"
                f"\n"
                f"[PARAGRAPH 1 — Introduction: I am writing to express my interest in the {clean_title} "
                f"at {company_name}. As a Bachelor's student in Computer Science at Berlin School of "
                f"Business and Innovation with 1+ year of hands-on experience in {kw_top_str}, I am "
                f"confident in my ability to contribute meaningfully to your team. Rewrite this naturally "
                f"in 2-3 sentences.]\n"
                f"\n"
                f"[PARAGRAPH 2 — Past Experience: Start with 'In my previous role at Soften Technologies, "
                f"I...'. Describe one concrete achievement: built Django/Flask web apps, designed REST APIs, "
                f"implemented RBAC, optimised PostgreSQL queries (60% latency cut). Mention soft skills like "
                f"teamwork, problem-solving, attention to detail. End by linking these to {company_name}'s "
                f"environment. 4-5 sentences.]\n"
                f"\n"
                f"[PARAGRAPH 3 — Why this company: Start with 'What excites me most about {company_name} is...'. "
                f"Pick ONE specific thing from the job description that genuinely fits ({job_description[:400] if job_description else 'their engineering focus'}). "
                f"State how the applicant's skills in {kw_top_str} can contribute. 3-4 sentences.]\n"
                f"\n"
                f"[PARAGRAPH 4 — Closing: I would welcome the opportunity to discuss how my background, "
                f"skills, and enthusiasm align with your needs. Thank you for considering my application, "
                f"and I look forward to the possibility of contributing to {company_name}'s success. "
                f"Rewrite naturally in 2 sentences.]\n"
                f"\n"
                f"Yours sincerely,\n"
                f"{name}\n\n"
                f"=== STRICT OUTPUT RULES ===\n"
                f"- Output ONLY the cover letter body. No preamble, no explanation.\n"
                f"- NO markdown (no **bold**, no #headers, no bullets).\n"
                f"- NO applicant header at the top (name/email/phone/address). The PDF generator adds those.\n"
                f"- Start the very first line with the date '{date_str}'.\n"
                f"- Use exactly the section breaks shown — blank line between every section.\n"
                f"- Write each paragraph as flowing prose, NOT as bullet points.\n"
                f"- Tone: professional, warm, confident. Avoid clichés like 'team player' or 'fast learner'.\n"
                f"- Length: 350-450 words total."
            )

            model = genai.GenerativeModel("gemini-2.5-flash-lite")
            response = model.generate_content(prompt)
            body = response.text.strip()
            # Strip accidental markdown if Gemini adds any
            body = re.sub(r'\*\*(.+?)\*\*', r'\1', body)
            body = re.sub(r'\*(.+?)\*', r'\1', body)
            body = re.sub(r'^#+\s*', '', body, flags=re.MULTILINE)
            body = re.sub(r'^[\-\*\u2022]\s+', '', body, flags=re.MULTILINE)
            return header + body

        except Exception as e:
            print(f"[WARNING] Gemini cover letter generation failed: {e} — using template fallback")

    # ── Fallback template — matches the 4-paragraph reference structure ───────
    kw_lower = [k.lower() for k in keywords]
    primary_skill = "Python full-stack development with Django and Flask"
    if any(k in kw_lower for k in ['javascript', 'react', 'node']) and not any(k in kw_lower for k in ['python', 'django']):
        primary_skill = "JavaScript/TypeScript development with React and Node.js"
    elif any(k in kw_lower for k in ['data', 'sql', 'analytics']):
        primary_skill = "data-driven backend development with Python and SQL"

    # Company-specific hook (pulled from job description if available)
    company_hook = ""
    if job_description and len(job_description) > 100:
        # Grab the first distinctive sentence from the JD
        first_sentence = re.split(r'(?<=[.!?])\s+', job_description[:300])[0]
        if len(first_sentence) > 40:
            company_hook = f"your focus on {first_sentence.lower().rstrip('.')[:120]}"
    if not company_hook:
        company_hook = "your engineering culture and the real impact your team has on production systems"

    return (
        f"{header}"
        f"{date_str}\n\n"
        f"{company_name}\n\n"
        f"Dear Hiring Team,\n\n"
        f"I am writing to express my interest in the {clean_title} at {company_name}. "
        f"As a Bachelor's student in Computer Science at Berlin School of Business and "
        f"Innovation with 1+ year of hands-on experience in {primary_skill}, I am confident "
        f"in my ability to contribute meaningfully to your team.\n\n"
        f"In my previous role at Soften Technologies as a Junior Python Full Stack Developer, "
        f"I built and maintained production Django and Flask applications serving 1,000+ daily "
        f"users, designed 20+ RESTful API endpoints with JWT authentication, and optimised "
        f"PostgreSQL queries to cut average response times by 60%. This work strengthened not "
        f"only my technical expertise but also essential soft skills such as teamwork, "
        f"problem-solving, and attention to detail. I have successfully delivered features "
        f"focused on security, scalability, and clean code \u2014 qualities I understand are "
        f"essential in a fast-paced environment like {company_name}.\n\n"
        f"What excites me most about {company_name} is {company_hook}. I am particularly "
        f"drawn to the opportunity to apply my skills in {kw_top_str} to help deliver real "
        f"value for your team, and I am eager to keep growing as an engineer alongside "
        f"experienced developers in a collaborative environment.\n\n"
        f"I would welcome the opportunity to discuss how my background, skills, and "
        f"enthusiasm align with your needs. Thank you for considering my application, and I "
        f"look forward to the possibility of contributing to {company_name}\u2019s success.\n\n"
        f"Yours sincerely,\n"
        f"{name}"
    )


# =============================================================================
# EMAIL NOTIFICATION
# =============================================================================

def send_application_email(match, personal_info, cv_buffer=None, cl_buffer=None,
                            company_name=None, job_title=None, job_url=None, ats_score=None):
    try:
        _defs = _applicant_defaults()
        company_name = (company_name or (match.company_name if match else '') or 'Company').strip()
        job_title    = (job_title    or (match.job_title    if match else '') or 'Position').strip()
        job_url      = job_url      or (getattr(match, 'job_url', '') if match else "")
        ats_score    = ats_score if ats_score is not None else (match.ats_score if match else 90)

        if cv_buffer is None or cl_buffer is None:
            try:
                resume_text = ""
                if match and match.job_query and match.job_query.base_cv:
                    resume_text = get_resume_text(match.job_query.base_cv.path)
                pi = extract_personal_info(resume_text) if resume_text else personal_info
                base = extract_cv_sections(resume_text)
                cv_sec, _ = build_tailored_cv(base, job_title, company_name, "", [])
                if cv_buffer is None:
                    cv_buffer = create_cv_pdf(cv_sec, pi)
                if cl_buffer is None:
                    cl_text = generate_tailored_cover_letter(pi, job_title, company_name, "", [])
                    cl_buffer = create_cover_letter_pdf(cl_text, pi, company_name, job_title)
            except Exception as e:
                log_event(match, "warning", f"Could not auto-generate PDFs: {e}")

        email_host = os.getenv("EMAIL_HOST", "smtp.gmail.com")
        email_port = int(os.getenv("EMAIL_PORT", "587"))
        email_user = os.getenv("EMAIL_HOST_USER")
        email_pass = os.getenv("EMAIL_HOST_PASSWORD")

        if not email_user or not email_pass:
            log_event(match, "error", "Email credentials not configured in .env")
            return False

        recipient = personal_info.get('email', email_user)

        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        msg = MIMEMultipart()
        msg['Subject'] = f"Job Application Submitted: {job_title} at {company_name}"
        msg['From']    = email_user
        msg['To']      = recipient

        body = (
            f"Dear {personal_info.get('name', _defs['name'])},\n\n"
            f"Your job application has been successfully submitted!\n\n"
            f"Company:    {company_name}\n"
            f"Position:   {job_title}\n"
            f"Platform:   LinkedIn\n"
            f"Job URL:    {job_url}\n"
            f"Date/Time:  {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}\n"
            f"ATS Score:  {ats_score}%\n\n"
            f"Attached:\n"
            f"- Tailored CV (PDF)\n"
            f"- Cover Letter (PDF)\n\n"
            f"Kind regards,\nAutomated Job Application System"
        )
        msg.attach(MIMEText(body, 'plain'))

        for buf, label in [(cv_buffer, 'CV'), (cl_buffer, 'CoverLetter')]:
            if buf:
                try:
                    buf.seek(0)
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(buf.read())
                    encoders.encode_base64(part)
                    applicant_clean = re.sub(
                        r'\W+', '', personal_info.get('name', _defs['name']))
                    part.add_header('Content-Disposition',
                                    f'attachment; filename="{applicant_clean}_{label}.pdf"')
                    msg.attach(part)
                except Exception as e:
                    log_event(match, "warning", f"{label} attachment error: {e}")

        with smtplib.SMTP(email_host, email_port, timeout=30) as smtp:
            smtp.ehlo(); smtp.starttls(); smtp.ehlo()
            smtp.login(email_user, email_pass)
            smtp.send_message(msg)

        log_event(match, "success", f"Email sent to {recipient}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        log_event(match, "error", f"Email auth failed — use Gmail App Password. Error: {e}")
        return False
    except Exception as e:
        log_event(match, "error", f"Email error: {e}")
        return False


# =============================================================================
# LINKEDIN LOGIN — fixed v4
# =============================================================================

def linkedin_login(driver, match, config):
    platform_config = PlatformConfig.objects.filter(platform='linkedin').first()
    if not platform_config or not platform_config.email_for_otp:
        log_event(match, "error", "LinkedIn credentials not found in PlatformConfig")
        return False

    email_val = platform_config.email_for_otp
    password  = platform_config.password

    EMAIL_CSS = (
        "#username,"
        "input[name='session_key'],"
        "input[autocomplete='username'],"
        "input[type='email']"
    )
    PASSWORD_CSS = (
        "#password,"
        "input[name='session_password'],"
        "input[autocomplete='current-password'],"
        "input[type='password']"
    )
    SUBMIT_CSS = (
        "button[type='submit'],"
        "button.sign-in-form__submit-button,"
        ".login__form_action_container button,"
        "button[data-litms-control-urn*='login-submit']"
    )
    ERROR_CSS = (
        ".alert-error,#error-for-username,#error-for-password,"
        ".login__error,[role='alert'],.form__input--error,"
        ".artdeco-inline-feedback--error"
    )

    LOGGED_IN_KEYS = ("/feed", "/mynetwork", "/jobs", "/in/", "/notifications", "/messaging")
    CHALLENGE_KEYS = ("challenge", "checkpoint", "captcha", "uas/consumer-mfa", "verify")

    def _ready(timeout=15):
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass

    def _classify_page():
        url = (driver.current_url or "").lower()
        if any(k in url for k in LOGGED_IN_KEYS):
            return "logged_in"
        if any(k in url for k in CHALLENGE_KEYS):
            return "challenge"
        try:
            if driver.find_elements(By.CSS_SELECTOR, EMAIL_CSS):
                return "login_form"
        except Exception:
            pass
        if "/login" in url or "/uas/login" in url:
            return "login_page_no_form"
        if "linkedin.com" in url and url.rstrip("/").endswith("linkedin.com"):
            return "home"
        return "unknown"

    def _wait_for_form(max_wait=25):
        end = time.time() + max_wait
        while time.time() < end:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, EMAIL_CSS):
                    if el.is_displayed():
                        return el
                el = driver.execute_script(
                    f"return document.querySelector(\"{EMAIL_CSS}\");"
                )
                if el:
                    return el
            except Exception:
                pass
            try:
                driver.execute_script("window.scrollTo(0, 250);")
            except Exception:
                pass
            time.sleep(1)
        return None

    def _check_login_error():
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, ERROR_CSS):
                if el.is_displayed() and (el.text or "").strip():
                    return el.text.strip()[:200]
        except Exception:
            pass
        return None

    def _fill(el, value):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            driver.execute_script("arguments[0].focus(); arguments[0].click();", el)
            time.sleep(0.2)
            driver.execute_script("""
                var nd = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value');
                nd.set.call(arguments[0], '');
                arguments[0].dispatchEvent(new Event('input', {bubbles:true}));
            """, el)
            el.send_keys(value)
            driver.execute_script("""
                arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));
                arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
                arguments[0].dispatchEvent(new Event('blur',   {bubbles:true}));
            """, el)
            time.sleep(0.4)
            return True
        except Exception as e:
            log_event(match, "warning", f"Fill failed: {e}")
            return False

    def _snapshot(tag=""):
        try:
            url = driver.current_url
            title = driver.title
            src = (driver.page_source or "")[:600].replace("\n", " ")
            log_event(match, "warning",
                      f"Snapshot[{tag}] url={url} title={title} src={src}")
        except Exception:
            pass

    LOGIN_URLS = [
        "https://www.linkedin.com/uas/login?trk=signin_via_native",
        "https://www.linkedin.com/login",
    ]

    for attempt in range(3):
        url_to_try = LOGIN_URLS[min(attempt, len(LOGIN_URLS) - 1)]
        try:
            log_event(match, "action",
                      f"Login attempt {attempt + 1}/3 → {url_to_try}")
            driver.get(url_to_try)
            _ready(20)
            time.sleep(3)
            handle_cookie_popup(driver, match)
            time.sleep(1.5)

            state = _classify_page()
            log_event(match, "info", f"Page state: {state} ({driver.current_url})")

            if state == "logged_in":
                log_event(match, "success", "Already logged in")
                return True

            if state == "challenge":
                log_event(match, "warning",
                          "Challenge/CAPTCHA detected — solve in browser (3 min)...")
                deadline = time.time() + 180
                while time.time() < deadline:
                    time.sleep(5)
                    if _classify_page() == "logged_in":
                        log_event(match, "success", "Challenge cleared")
                        return True
                log_event(match, "error", "Challenge timed out")
                return False

            if state == "home":
                try:
                    btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR,
                            "a[href*='/login'],a.nav__button-secondary,"
                            "a[data-tracking-control-name*='signin']"))
                    )
                    btn.click()
                    _ready(15)
                    time.sleep(2)
                    handle_cookie_popup(driver, match)
                except Exception:
                    pass

            email_field = _wait_for_form(max_wait=25)
            if not email_field:
                log_event(match, "warning",
                          f"Login form did not mount on attempt {attempt + 1}")
                _snapshot("no_form")
                time.sleep(3)
                continue

            if not _fill(email_field, email_val):
                _snapshot("email_fill_fail")
                continue

            password_field = None
            for _ in range(8):
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, PASSWORD_CSS):
                        if el.is_displayed():
                            password_field = el
                            break
                    if not password_field:
                        password_field = driver.execute_script(
                            f"return document.querySelector(\"{PASSWORD_CSS}\");"
                        )
                    if password_field:
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            if not password_field:
                log_event(match, "warning", "Password field not found")
                _snapshot("no_password")
                continue

            if not _fill(password_field, password):
                _snapshot("password_fill_fail")
                continue

            submitted = False
            try:
                for btn in driver.find_elements(By.CSS_SELECTOR, SUBMIT_CSS):
                    if btn.is_displayed() and btn.is_enabled():
                        driver.execute_script("arguments[0].click();", btn)
                        submitted = True
                        log_event(match, "info", "Submit clicked")
                        break
            except Exception:
                pass
            if not submitted:
                try:
                    password_field.send_keys(Keys.RETURN)
                    submitted = True
                    log_event(match, "info", "Submitted via Enter key")
                except Exception:
                    pass
            if not submitted:
                log_event(match, "warning", "Could not submit form")
                continue

            log_event(match, "action", "Awaiting login response...")
            deadline = time.time() + 25
            final_state = "unknown"
            while time.time() < deadline:
                time.sleep(2)
                err = _check_login_error()
                if err:
                    log_event(match, "error", f"LinkedIn rejected login: {err}")
                    return False
                final_state = _classify_page()
                if final_state in ("logged_in", "challenge"):
                    break

            if final_state == "logged_in":
                log_event(match, "success", "LinkedIn login successful")
                return True

            if final_state == "challenge":
                log_event(match, "warning",
                          "Verification required — solve in browser (3 min)...")
                deadline = time.time() + 180
                while time.time() < deadline:
                    time.sleep(5)
                    if _classify_page() == "logged_in":
                        log_event(match, "success", "Verification passed")
                        return True
                log_event(match, "error", "Verification timed out")
                return False

            log_event(match, "warning",
                      f"Unexpected post-submit state: {final_state} "
                      f"({driver.current_url}) — retrying")
            _snapshot("post_submit")
            time.sleep(3)

        except InvalidSessionIdException:
            log_event(match, "error",
                      "Chrome session crashed — check Chrome/ChromeDriver versions")
            return False
        except Exception as e:
            import traceback
            log_event(match, "error",
                      f"Login attempt {attempt + 1} error: "
                      f"{type(e).__name__}: {e}")
            log_event(match, "error", traceback.format_exc()[:400])
            time.sleep(3)

    log_event(match, "error", "All 3 login attempts failed")
    return False


# =============================================================================
# JOB CARD HELPERS
# =============================================================================

def get_job_cards_from_page(driver, match=None):
    try:
        list_container = None
        for list_sel in [
            ".jobs-search-results-list",
            ".scaffold-layout__list",
            "ul.jobs-search-results__list",
            ".jobs-search-results__list",
        ]:
            try:
                list_container = driver.find_element(By.CSS_SELECTOR, list_sel)
                break
            except Exception:
                continue

        if list_container:
            for _ in range(3):
                driver.execute_script("arguments[0].scrollTop += 600;", list_container)
                time.sleep(0.8)
            driver.execute_script("arguments[0].scrollTop = 0;", list_container)
        else:
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
            driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
    except Exception:
        pass

    CARD_SELECTORS = [
        "li.jobs-search-results__list-item",
        "div.job-card-container--clickable",
        "div[data-occludable-job-id]",
        "li[data-occludable-entity-urn]",
        "div.job-card-list__entity-lockup",
        "div.job-card-container",
        "li.scaffold-layout__list-item",
        "div[data-job-id]",
        "div.base-card",
        "div.base-search-card",
        "div.job-search-card",
        "ul.jobs-search-results__list > li",
        ".jobs-search-results-list li",
        "div.scaffold-layout__list li",
    ]

    for sel in CARD_SELECTORS:
        try:
            cards   = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [card for card in cards if card.is_displayed()]
            if visible:
                if match:
                    log_event(match, "info", f"Found {len(visible)} job cards via: {sel}")
                return visible
        except Exception:
            continue

    if match:
        try:
            src = driver.page_source[:800].replace('\n', ' ')
            log_event(match, "warning", f"No cards found. Page snapshot: {src}")
        except Exception:
            log_event(match, "warning", "No cards found (page snapshot unavailable)")
    return []


def get_job_details_from_page(driver, match):
    try:
        details = {
            'title': '', 'company': '', 'description': '',
            'url': driver.current_url, 'is_easy_apply': False, 'has_apply_button': False
        }
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "div.jobs-unified-top-card,"
                    "div.job-view-layout,"
                    "div.jobs-details,"
                    "div[class*='jobs-details']"))
            )
        except Exception:
            pass
        time.sleep(2)
        enable_translation_on_page(driver, match)

        for sel in [
            "h1.t-24", "h1.jobs-unified-top-card__job-title",
            "h2.jobs-unified-top-card__job-title",
            "h1.job-details-jobs-unified-top-card__job-title",
            "h1[class*='job-title']", "h1",
        ]:
            try:
                details['title'] = driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                if details['title']:
                    break
            except Exception:
                continue
        if not details['title']:
            details['title'] = "Unknown Position"

        for sel in [
            "a.jobs-unified-top-card__company-name",
            "span.jobs-unified-top-card__company-name",
            ".jobs-unified-top-card__primary-description a",
            "div.job-details-jobs-unified-top-card__company-name a",
            "[class*='company-name']",
        ]:
            try:
                details['company'] = driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                if details['company']:
                    break
            except Exception:
                continue
        if not details['company']:
            details['company'] = "Unknown Company"

        try:
            try:
                sm = driver.find_element(By.XPATH, "//button[contains(text(),'Show more')]")
                if sm.is_displayed():
                    driver.execute_script("arguments[0].click();", sm)
                    time.sleep(1)
            except Exception:
                pass
            for desc_sel in [
                "div.jobs-description__content",
                "div.jobs-box__html-content",
                "div.jobs-description",
                "div[class*='description__text']",
            ]:
                try:
                    details['description'] = driver.find_element(
                        By.CSS_SELECTOR, desc_sel).text.strip()
                    if details['description']:
                        break
                except Exception:
                    continue
        except Exception:
            pass

        try:
            eb = driver.find_element(By.CSS_SELECTOR,
                "button.jobs-apply-button[aria-label*='Easy Apply'],"
                "button[aria-label*='Easy Apply']")
            if eb.is_displayed():
                details['is_easy_apply'] = True
        except Exception:
            pass

        try:
            for b in driver.find_elements(By.CSS_SELECTOR,
                "button.jobs-apply-button:not([aria-label*='Easy Apply']),"
                "a.jobs-apply-button,"
                "button[aria-label*='Apply']:not([aria-label*='Easy'])"):
                if b.is_displayed():
                    details['has_apply_button'] = True
                    break
        except Exception:
            pass

        return details
    except Exception as e:
        if match:
            log_event(match, "error", f"Error getting job details: {e}")
        return None


def click_job_card_safely(driver, job_index, match=None):
    for attempt in range(3):
        try:
            cards = get_job_cards_from_page(driver, match)
            if job_index >= len(cards):
                return False, "Index out of range"
            card = cards[job_index]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
            time.sleep(0.5)
            try:
                card.click()
            except (ElementClickInterceptedException, ElementNotInteractableException):
                driver.execute_script("arguments[0].click();", card)
            time.sleep(3)
            return True, "Success"
        except StaleElementReferenceException:
            if attempt < 2:
                time.sleep(2); continue
            return False, "Stale element"
        except Exception as e:
            if attempt < 2:
                time.sleep(2); continue
            return False, str(e)
    return False, "Max attempts"


# =============================================================================
# APPLY BUTTON — v2
# =============================================================================

def click_external_apply(driver, match):
    BAD = ['subscribe', 'newsletter', 'alert', 'save', 'follow', 'share',
           'easy apply', 'einfach bewerben', 'merken', 'teilen', 'speichern',
           # — "apply later" / "send me the link" variants (join.com, etc.)
           'apply later', 'save for later', 'remind me', 'remind',
           'request link', 'send link', 'send me', 'link anfordern',
           'link zusenden', 'link per email', 'link per e-mail', 'per mail',
           'per e-mail', 'erinnern', 'erinnerung', 'merkliste',
           'apply on mobile', 'auf mobilgerät', 'später', 'spaeter',
           'später bewerben', 'spaeter bewerben', 'bewerbung speichern']

    def _is_easy_apply_btn(btn):
        try:
            txt = ((btn.text or '') + ' ' +
                   (btn.get_attribute('aria-label') or '')).lower()
            return 'easy apply' in txt or 'einfach bewerben' in txt
        except Exception:
            return False

    def _is_apply_btn(btn):
        try:
            txt = ((btn.text or '') + ' ' +
                   (btn.get_attribute('aria-label') or '')).lower()
            if any(b in txt for b in BAD):
                return False
            return ('apply' in txt or 'bewerben' in txt) and not _is_easy_apply_btn(btn)
        except Exception:
            return False

    def _click_btn(btn, label):
        try:
            initial_handles = len(driver.window_handles)
            initial_url = driver.current_url
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.5)
            try:
                btn.click()
            except (ElementClickInterceptedException,
                    ElementNotInteractableException):
                driver.execute_script("arguments[0].click();", btn)
            deadline = time.time() + 8
            navigated = False
            while time.time() < deadline:
                time.sleep(0.4)
                try:
                    if len(driver.window_handles) > initial_handles:
                        navigated = True
                        break
                    if driver.current_url != initial_url:
                        navigated = True
                        break
                except Exception:
                    pass
            handle_cookie_popup(driver, match)
            log_event(match, "success",
                      f"Apply clicked via: {label} (navigated={navigated})")
            return True
        except Exception as e:
            log_event(match, "warning", f"Apply click error ({label}): {e}")
            return False

    try:
        handle_cookie_popup(driver, match)

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "div.jobs-unified-top-card,"
                    "div.job-details-jobs-unified-top-card__container--two-pane,"
                    "div.job-view-layout,"
                    "div.jobs-details,"
                    "div[class*='jobs-details']"))
            )
        except Exception:
            pass
        time.sleep(2)

        try:
            top_card = driver.find_element(By.CSS_SELECTOR,
                "div.jobs-unified-top-card,"
                "div.job-details-jobs-unified-top-card__container--two-pane,"
                "div.jobs-details")
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'start'});", top_card)
            time.sleep(0.6)
        except Exception:
            pass

        APPLY_SELECTORS = [
            "button.jobs-apply-button",
            "a.jobs-apply-button",
            "button.jobs-apply-button--top-card",
            ".jobs-s-apply button",
            ".jobs-apply-button--top-card button",
            "button[aria-label*='Apply']",
            "button[aria-label*='apply']",
            "a[aria-label*='Apply']",
            "button[aria-label*='Bewerben']",
            "button[aria-label*='bewerben']",
            "button[data-control-name='jobdetails_topcard_apply']",
            "button[data-control-name*='apply']",
            "a[data-control-name*='apply']",
            ".jobs-unified-top-card button.artdeco-button--primary",
            ".job-details-jobs-unified-top-card__container--two-pane "
                "button.artdeco-button--primary",
        ]

        easy_apply_found = []
        external_apply_found = []
        seen_ids = set()

        for sel in APPLY_SELECTORS:
            try:
                for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                    try:
                        bid = id(btn)
                        if bid in seen_ids:
                            continue
                        seen_ids.add(bid)
                        if not btn.is_displayed() or not btn.is_enabled():
                            continue
                        if 'disabled' in (btn.get_attribute('class') or ''):
                            continue
                        if _is_easy_apply_btn(btn):
                            easy_apply_found.append((btn, sel))
                        elif _is_apply_btn(btn):
                            external_apply_found.append((btn, sel))
                    except Exception:
                        continue
            except Exception:
                continue

        log_event(match, "info",
                  f"Apply scan — easy={len(easy_apply_found)} "
                  f"external={len(external_apply_found)}")

        if easy_apply_found and not external_apply_found:
            return False, "Easy Apply only"

        for btn, sel in external_apply_found:
            if _click_btn(btn, sel):
                return True, "Clicked"

        UP = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        LO = 'abcdefghijklmnopqrstuvwxyz'

        TEXT_VARIANTS = [
            'apply', 'apply now', 'apply on company website',
            'apply on company site', 'apply on employer site',
            'apply externally', 'continue to apply',
            'bewerben', 'jetzt bewerben', 'extern bewerben',
            'auf unternehmenswebsite bewerben',
        ]

        for text in TEXT_VARIANTS:
            xpath = (
                f"//button[translate(normalize-space(.),'{UP}','{LO}')='{text}']"
                f"|//a[translate(normalize-space(.),'{UP}','{LO}')='{text}']"
                f"|//button[contains(translate(normalize-space(.),"
                f"'{UP}','{LO}'),'{text}')]"
                f"|//a[contains(translate(normalize-space(.),"
                f"'{UP}','{LO}'),'{text}')]"
            )
            try:
                for btn in driver.find_elements(By.XPATH, xpath):
                    try:
                        if not btn.is_displayed() or not btn.is_enabled():
                            continue
                        if _is_easy_apply_btn(btn):
                            continue
                        if 'disabled' in (btn.get_attribute('class') or ''):
                            continue
                        txt_l = (btn.text or '').lower()
                        if any(b in txt_l for b in BAD):
                            continue
                        if _click_btn(btn, f"xpath:{text}"):
                            return True, "Clicked"
                    except Exception:
                        continue
            except Exception:
                continue

        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            iframes = []

        for idx in range(len(iframes)):
            try:
                fresh = driver.find_elements(By.TAG_NAME, "iframe")
                if idx >= len(fresh):
                    break
                driver.switch_to.default_content()
                driver.switch_to.frame(fresh[idx])
                time.sleep(0.5)
                for sel in APPLY_SELECTORS:
                    try:
                        for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                            try:
                                if (not btn.is_displayed()
                                        or not btn.is_enabled()
                                        or _is_easy_apply_btn(btn)
                                        or not _is_apply_btn(btn)):
                                    continue
                                if _click_btn(btn, f"iframe:{sel}"):
                                    driver.switch_to.default_content()
                                    return True, "Clicked (iframe)"
                            except Exception:
                                continue
                    except Exception:
                        continue
            except Exception:
                pass
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

        try:
            visible_buttons = []
            for btn in driver.find_elements(By.TAG_NAME, "button")[:20]:
                try:
                    if btn.is_displayed():
                        t = (btn.text or btn.get_attribute('aria-label') or '').strip()
                        if t and len(t) < 50:
                            visible_buttons.append(t)
                except Exception:
                    continue
            log_event(match, "warning",
                      f"No external apply button. Visible buttons: "
                      f"{visible_buttons[:10]}")
        except Exception:
            pass

        return False, "No external apply button found"

    except Exception as e:
        log_event(match, "error", f"click_external_apply error: {e}")
        return False, str(e)


# =============================================================================
# SMART FORM FILLER v3
# =============================================================================

_JS_REACT_INPUT = """
(function(el, val){
    var nd = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    if(nd && nd.set){ nd.set.call(el, val); }
    else { el.value = val; }
    el.dispatchEvent(new Event('input',  {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    el.dispatchEvent(new Event('blur',   {bubbles:true}));
})(arguments[0], arguments[1]);
"""

_JS_REACT_TEXTAREA = """
(function(el, val){
    var nd = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
    if(nd && nd.set){ nd.set.call(el, val); }
    else { el.value = val; }
    el.dispatchEvent(new Event('input',  {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    el.dispatchEvent(new Event('blur',   {bubbles:true}));
})(arguments[0], arguments[1]);
"""

_FIELD_CSS = (
    "input[type='text'],input[type='email'],input[type='tel'],"
    "input[type='url'],input[type='number'],input[type='search'],"
    "input:not([type]),"
    "textarea,select,"
    "[role='combobox'],[role='listbox'],"
    "[aria-haspopup='listbox'],[aria-haspopup='menu']"
)


def _get_label_text(driver, field):
    parts = []
    fid = field.get_attribute('id') or ''
    if fid:
        try:
            parts.append(driver.find_element(By.CSS_SELECTOR, f"label[for='{fid}']").text)
        except Exception:
            pass
    for lid in (field.get_attribute('aria-labelledby') or '').split():
        try:
            parts.append(driver.find_element(By.ID, lid).text)
        except Exception:
            pass
    for did in (field.get_attribute('aria-describedby') or '').split():
        try:
            parts.append(driver.find_element(By.ID, did).text)
        except Exception:
            pass
    try:
        parts.append(field.find_element(By.XPATH, "./ancestor::label[1]").text)
    except Exception:
        pass
    try:
        parts.append(field.find_element(
            By.XPATH,
            "./preceding-sibling::label[1]|"
            "./preceding-sibling::span[1]|"
            "./preceding-sibling::div[1]"
        ).text)
    except Exception:
        pass
    try:
        t = driver.execute_script(
            "var t=''; arguments[0].parentNode.childNodes.forEach("
            "function(n){if(n.nodeType===3)t+=n.textContent;}); return t;",
            field) or ''
        parts.append(t)
    except Exception:
        pass
    try:
        t = driver.execute_script(
            "var p=arguments[0].parentNode; if(!p)return '';"
            "var g=p.parentNode; if(!g)return '';"
            "var f=g.firstElementChild; return f?f.innerText:'';",
            field) or ''
        parts.append(t)
    except Exception:
        pass
    try:
        parts.append(field.find_element(By.XPATH, "./ancestor::fieldset[1]/legend[1]").text)
    except Exception:
        pass
    return ' '.join(parts).lower()


def _smart_fill(driver, field, value):
    val = str(value)
    tag = field.tag_name.lower()
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
        time.sleep(0.1)
    except Exception:
        pass
    try:
        script = _JS_REACT_TEXTAREA if tag == 'textarea' else _JS_REACT_INPUT
        driver.execute_script(script, field, val)
        time.sleep(0.1)
        if (field.get_attribute('value') or '').strip():
            return True
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].value=arguments[1];", field, val)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('blur',{bubbles:true}));", field)
        time.sleep(0.1)
        if (field.get_attribute('value') or '').strip():
            return True
    except Exception:
        pass
    try:
        field.click(); time.sleep(0.1)
        field.clear()
        field.send_keys(Keys.CONTROL + 'a')
        field.send_keys(Keys.DELETE)
        for ch in val:
            field.send_keys(ch); time.sleep(0.02)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('blur',{bubbles:true}));", field)
        time.sleep(0.1)
        if (field.get_attribute('value') or '').strip():
            return True
    except Exception:
        pass
    try:
        driver.execute_script(
            "arguments[0].focus();"
            "document.execCommand('selectAll');"
            "document.execCommand('insertText',false,arguments[1]);", field, val)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", field)
        return True
    except Exception:
        pass
    return False


def _fill_custom_dropdown(driver, field, value, match, alternatives=None):
    """Handle non-native dropdowns: role='combobox', aria-haspopup='listbox',
    React-select, Workday, Greenhouse, Lever, etc. Returns True if a matching
    option was clicked."""
    candidates = [str(value)] + (alternatives or [])
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", field)
        time.sleep(0.3)
        try:
            field.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            driver.execute_script("arguments[0].click();", field)
        time.sleep(0.8)
    except Exception:
        return False

    LISTBOX_SELS = [
        "ul[role='listbox'] li",
        "div[role='listbox'] [role='option']",
        "[role='option']",
        "ul.select__menu-list li",
        ".css-1n7v3ny-option",
        "[data-automation-id='promptOption']",
        "[class*='react-select__option']",
        "[class*='Select__option']",
        ".dropdown-menu li",
        ".dropdown-menu a",
        "[class*='dropdown'] [class*='option']",
        "[class*='dropdown'] [class*='item']",
        ".select2-results__option",
        "[class*='Listbox'] [class*='Option']",
        ".chakra-menu__menu-list button",
    ]

    def _options_now():
        opts = []
        for sel in LISTBOX_SELS:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    try:
                        if el.is_displayed() and (el.text or "").strip():
                            opts.append(el)
                    except Exception:
                        continue
            except Exception:
                continue
        seen = set()
        uniq = []
        for el in opts:
            try:
                k = id(el)
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(el)
            except Exception:
                continue
        return uniq

    options = []
    for _ in range(6):
        options = _options_now()
        if options:
            break
        time.sleep(0.4)

    if not options:
        try:
            field.send_keys(Keys.ESCAPE)
        except Exception:
            pass
        return False

    for cand in candidates:
        cand_l = cand.lower().strip()
        for opt in options:
            if (opt.text or "").strip().lower() == cand_l:
                try:
                    driver.execute_script("arguments[0].click();", opt)
                    time.sleep(0.5)
                    log_event(match, "info",
                              f"Custom dropdown: selected '{opt.text[:40]}'")
                    return True
                except Exception:
                    continue
        for opt in options:
            ot = (opt.text or "").lower()
            if cand_l in ot or ot in cand_l:
                try:
                    driver.execute_script("arguments[0].click();", opt)
                    time.sleep(0.5)
                    log_event(match, "info",
                              f"Custom dropdown (partial): '{opt.text[:40]}'")
                    return True
                except Exception:
                    continue

    try:
        field.send_keys(candidates[0])
        time.sleep(0.8)
        for opt in _options_now():
            try:
                driver.execute_script("arguments[0].click();", opt)
                time.sleep(0.5)
                log_event(match, "info",
                          f"Custom dropdown (filtered): '{opt.text[:40]}'")
                return True
            except Exception:
                continue
    except Exception:
        pass

    try:
        field.send_keys(Keys.ESCAPE)
    except Exception:
        pass
    return False


def _fill_select(driver, field, value, alternatives=None):
    candidates = [value] + (alternatives or [])
    try:
        s = Select(field)
        opts = [o.text.strip() for o in s.options]
        for cand in candidates:
            try:
                s.select_by_visible_text(cand); return True
            except Exception:
                pass
            for opt in opts:
                if cand.lower() in opt.lower() or opt.lower() in cand.lower():
                    try:
                        s.select_by_visible_text(opt); return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False


def _fill_autocomplete(driver, field, value, match):
    try:
        _smart_fill(driver, field, value[:5])
        time.sleep(1.8)
        DROPDOWN_SELECTORS = [
            ".pac-item", "[class*='react-select__option']",
            "[data-automation-id='promptOption']", "ul[role='listbox'] li",
            "div[role='option']", "[role='option']", ".dropdown-item",
            ".autocomplete-suggestion", "[class*='suggestion']",
            "[class*='autocomplete'] li",
        ]
        for sel in DROPDOWN_SELECTORS:
            try:
                suggs = [el for el in driver.find_elements(By.CSS_SELECTOR, sel)
                         if el.is_displayed()]
                if not suggs:
                    continue
                best = next(
                    (el for el in suggs if value.lower()[:5] in (el.text or '').lower()),
                    suggs[0]
                )
                driver.execute_script("arguments[0].click();", best)
                time.sleep(0.6)
                return True
            except Exception:
                continue
        _smart_fill(driver, field, value)
        field.send_keys(Keys.TAB)
        return True
    except Exception:
        return False


def _upload_cv(driver, cv_path, match):
    if not cv_path or not os.path.exists(cv_path):
        return False
    try:
        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
    except Exception:
        return False
    for fi in file_inputs:
        try:
            driver.execute_script(
                "arguments[0].style.display='block';"
                "arguments[0].style.visibility='visible';"
                "arguments[0].style.opacity='1';"
                "arguments[0].removeAttribute('hidden');", fi)
            time.sleep(0.3)
            fi.send_keys(cv_path)
            time.sleep(2)
            log_event(match, "success", "CV uploaded (S1)")
            return True
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].click();", fi)
            time.sleep(0.5)
            fi.send_keys(cv_path)
            time.sleep(2)
            log_event(match, "success", "CV uploaded (S2)")
            return True
        except Exception:
            pass

    UP = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    LO = 'abcdefghijklmnopqrstuvwxyz'
    for xpath in [
        f"//button[contains(translate(.,' {UP}','{LO}'),'upload')]",
        f"//label[contains(translate(.,' {UP}','{LO}'),'upload')]",
        f"//button[contains(translate(.,' {UP}','{LO}'),'resume')]",
        f"//button[contains(translate(.,' {UP}','{LO}'),'cv')]",
        f"//label[contains(translate(.,' {UP}','{LO}'),'resume')]",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            if btn.is_displayed():
                btn.click(); time.sleep(1.2)
                for fi in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
                    try:
                        driver.execute_script(
                            "arguments[0].style.display='block';"
                            "arguments[0].style.visibility='visible';", fi)
                        fi.send_keys(cv_path)
                        time.sleep(2)
                        log_event(match, "success", "CV uploaded (S3)")
                        return True
                    except Exception:
                        continue
        except Exception:
            continue

    log_event(match, "info", "CV upload — no file input on this step (skipping)")
    return False


def select_country_code(driver, match, country_code="+49"):
    try:
        for sel_el in driver.find_elements(By.CSS_SELECTOR,
                "select[name*='country'], select[name*='code'], "
                "select[id*='country'], select[id*='code']"):
            if not sel_el.is_displayed():
                continue
            try:
                s = Select(sel_el)
                for opt in s.options:
                    if any(x in opt.text for x in ['+49', 'Germany', 'DE']):
                        s.select_by_visible_text(opt.text)
                        return True
            except Exception:
                continue
        for xpath in [
            "//div[contains(@class,'flag')]",
            "//div[contains(@class,'country-code')]",
            "//div[contains(@class,'PhoneInputCountry')]",
            "//input[@type='tel']/preceding-sibling::div",
        ]:
            try:
                dd = driver.find_element(By.XPATH, xpath)
                if not dd.is_displayed():
                    continue
                dd.click(); time.sleep(1.2)
                for opt_sel in [
                    f"//li[contains(text(),'{country_code}')]",
                    "//li[contains(text(),'Germany')]",
                    "//li[contains(text(),'Deutschland')]",
                ]:
                    try:
                        driver.find_element(By.XPATH, opt_sel).click()
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    return False


def _score_field(id_name_str, label, aria, placeholder, aria_ph,
                 itype, tag, patterns, excludes, ftype):
    if any(ex in id_name_str for ex in excludes):
        return 0
    combined = ' '.join([id_name_str, label, aria, placeholder, aria_ph])
    s = 0
    fid_part   = id_name_str.split('|')[0] if '|' in id_name_str else id_name_str
    fname_part = id_name_str.split('|')[1] if '|' in id_name_str else id_name_str
    for p in patterns:
        if p not in combined:
            continue
        if p == fid_part or p == fname_part:    s += 12
        elif p in fid_part or p in fname_part:  s += 10
        elif p in label:                         s += 9
        elif p in aria:                          s += 8
        elif p in placeholder or p in aria_ph:  s += 5
        else:                                    s += 2
    if ftype == 'email'             and itype == 'email':    s += 8
    if ftype == 'phone'             and itype == 'tel':      s += 8
    if ftype == 'cover_letter_text' and tag == 'textarea':   s += 6
    if ftype in ('website', 'linkedin') and itype == 'url':  s += 5
    return s


def _process_frame_fields(driver, match, FIELDS, filled_set, PHONE_LOCAL, PHONE_FULL):
    filled = 0

    def field_key(el):
        fid = el.get_attribute('id') or ''
        fn  = el.get_attribute('name') or ''
        try:
            html_hash = 'h_' + str(hash((el.get_attribute('outerHTML') or '')[:300]))
        except Exception:
            html_hash = None
        if fid:
            return f'id_{fid}_{html_hash or str(id(el))}'
        if fn:
            return f'nm_{fn}_{html_hash or str(id(el))}'
        return html_hash or ('mem_' + str(id(el)))

    try:
        fields = driver.find_elements(By.CSS_SELECTOR, _FIELD_CSS)
    except Exception:
        return 0

    for field in fields:
        try:
            if not field.is_displayed():
                continue
            if field.get_attribute('disabled') or field.get_attribute('readonly'):
                continue
            fk = field_key(field)
            if fk in filled_set:
                continue

            fid   = (field.get_attribute('id')               or '').lower()
            fname = (field.get_attribute('name')             or '').lower()
            fph   = (field.get_attribute('placeholder')      or '').lower()
            ftype = (field.get_attribute('type')             or 'text').lower()
            aria  = (field.get_attribute('aria-label')       or '').lower()
            ariap = (field.get_attribute('aria-placeholder') or '').lower()
            tag   = field.tag_name.lower()

            UI_BLACKLIST = [
                'jobs-search-box', 'job-search-box', 'search-box-keyword',
                'search-box-location', 'global-nav-search', 'search-global',
                'nav-search', 'ember',
                'jobs-search-results', 'search-keywords-typeahead',
                'search-location-typeahead',
            ]
            if any(bl in fid for bl in UI_BLACKLIST) and not fname:
                continue
            if ftype in ('submit', 'button', 'hidden', 'image', 'reset',
                         'checkbox', 'radio', 'file'):
                continue

            label       = _get_label_text(driver, field)
            id_name_str = f"{fid}|{fname}"

            best = None; best_s = 0
            for ft, cfg in FIELDS.items():
                s = _score_field(id_name_str, label, aria, fph, ariap,
                                 ftype, tag, cfg['patterns'], cfg['exclude'], ft)
                if s > best_s:
                    best_s = s; best = ft

            min_score = 3 if tag == 'select' else 5

            if not best or best_s < min_score:
                if tag == 'select':
                    _handle_unidentified_select(driver, field, label, match)
                else:
                    log_event(match, "warning",
                              f"Unidentified (score={best_s}): "
                              f"id={fid} name={fname} label={label[:50]}")
                continue

            cfg = FIELDS[best]
            val = cfg['value']
            log_event(match, "info", f"Filling '{best}' (score={best_s}): {str(val)[:35]}")

            role  = (field.get_attribute('role') or '').lower()
            popup = (field.get_attribute('aria-haspopup') or '').lower()
            is_custom_dropdown = (
                role in ('combobox', 'listbox') or
                popup in ('listbox', 'menu', 'true') or
                'select' in (field.get_attribute('class') or '').lower() and tag != 'input'
            )

            ok = False
            if tag == 'select':
                ok = _fill_select(driver, field, val, cfg.get('select_alternatives'))
                if not ok and cfg.get('select_alternatives'):
                    ok = _fill_custom_dropdown(
                        driver, field, val, match, cfg.get('select_alternatives'))
            elif is_custom_dropdown and cfg.get('select_alternatives'):
                ok = _fill_custom_dropdown(
                    driver, field, val, match, cfg.get('select_alternatives'))
                if not ok:
                    ok = _smart_fill(driver, field, val)
            elif best == 'phone':
                has_code = any(x in (fid + fname + fph + aria)
                               for x in ['+49', 'country code', 'country-code',
                                         'country_code', 'countrycode'])
                phone_formats = (
                    [PHONE_LOCAL, PHONE_FULL, PHONE_FULL.replace('+49', '+49 ')[:16], '0' + PHONE_LOCAL, PHONE_LOCAL]
                    if has_code else
                    [PHONE_FULL, PHONE_FULL.replace('+49', '+49 ')[:16], PHONE_LOCAL, '0' + PHONE_LOCAL, PHONE_LOCAL]
                )
                for fmt in phone_formats:
                    ok = _smart_fill(driver, field, fmt)
                    if ok:
                        break
            elif cfg.get('auto'):
                ok = _fill_autocomplete(driver, field, val, match)
                if not ok:
                    ok = _smart_fill(driver, field, cfg.get('value_full', val))
            else:
                ok = _smart_fill(driver, field, val)

            if ok:
                filled_set.add(fk)
                filled += 1
            else:
                log_event(match, "warning", f"Fill failed for '{best}'")

        except StaleElementReferenceException:
            continue
        except Exception as ex:
            log_event(match, "warning", f"Field error: {ex}")
            continue

    try:
        for radio in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            try:
                if not radio.is_displayed():
                    continue
                rn = (radio.get_attribute('name')  or '').lower()
                ri = (radio.get_attribute('id')    or '').lower()
                rv = (radio.get_attribute('value') or '').lower()
                rl = _get_label_text(driver, radio)
                kw = ['visa', 'sponsor', 'authoriz', 'authoris', 'work permit',
                      'right to work', 'arbeitserlaubnis', 'berechtigt', 'eu citizen',
                      'eligible', 'work in']
                if any(k in rn or k in ri or k in rl for k in kw):
                    if rv in ('yes', 'true', '1', 'ja', 'y') and not radio.is_selected():
                        driver.execute_script("arguments[0].click();", radio)
                        filled += 1
                        log_event(match, "info", "Radio: Yes for work authorization")
            except Exception:
                continue
    except Exception as e:
        log_event(match, "warning", f"Radio error: {e}")

    try:
        for cb in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            try:
                if not cb.is_displayed():
                    continue
                cbi = (cb.get_attribute('id')   or '').lower()
                cbn = (cb.get_attribute('name') or '').lower()
                cbl = _get_label_text(driver, cb)
                kw = ['consent', 'agree', 'terms', 'privacy', 'gdpr', 'dsgvo',
                      'datenschutz', 'einwilligung', 'zustimmung', 'accept',
                      'i have read', 'ich habe']
                if any(k in cbi or k in cbn or k in cbl for k in kw):
                    if not cb.is_selected():
                        driver.execute_script("arguments[0].click();", cb)
                        filled += 1
                        log_event(match, "info", "Checkbox: consent accepted")
            except Exception:
                continue
    except Exception as e:
        log_event(match, "warning", f"Checkbox error: {e}")

    return filled


def _handle_unidentified_select(driver, field, label, match):
    try:
        sel = Select(field)
        opts = [o.text.strip() for o in sel.options if o.text.strip()
                and o.text.strip() not in ('--', '---', 'Select', 'Please select',
                                           'Bitte auswahlen', '-', 'None')]
        if not opts:
            return
        label_l = label.lower()
        KEYWORD_MAP = [
            (['internship','praktikum','type','art'],
             ['internship','working student','werkstudent','praktikum','part-time','student']),
            (['gender','sex','geschlecht','anrede'],
             ['male','herr','mr','mann','männlich']),
            (['linkedin','source','hear','channel','referral'],
             ['linkedin','job board','online','internet','website','other']),
            (['education','degree','qualification'], ["bachelor","undergraduate","bsc"]),
            (['disability','behinderung'], ['no','nein','none','not applicable']),
            (['language','english','proficiency'], ['c1','advanced','b2','fluent','professional']),
            (['nationality','citizenship'], ['india','indian','other']),
            (['notice','start','available'], ['immediately','sofort','1 month','2 weeks','asap']),
            (['country','residence','location'], ['germany','deutschland','de']),
            (['yes','no','agree','consent'], ['yes','ja','agree']),
        ]
        chosen = None
        for label_kws, pref_kws in KEYWORD_MAP:
            if any(kw in label_l for kw in label_kws):
                for pref in pref_kws:
                    for opt in opts:
                        if pref.lower() in opt.lower():
                            chosen = opt
                            break
                    if chosen:
                        break
                break
        if not chosen:
            chosen = opts[0]
        try:
            sel.select_by_visible_text(chosen)
            log_event(match, "info", f"Catch-all select '{chosen}' for label: {label[:50]}")
        except Exception:
            for opt in opts:
                if chosen.lower() in opt.lower() or opt.lower() in chosen.lower():
                    try:
                        sel.select_by_visible_text(opt)
                        log_event(match, "info",
                                  f"Catch-all select (partial) '{opt}' for: {label[:50]}")
                        break
                    except Exception:
                        continue
    except Exception as e:
        log_event(match, "warning", f"Catch-all select error: {e}")


def fill_application_form(driver, match, personal_info, cv_path, job_keywords):
    _defs = _applicant_defaults()
    log_event(match, "action", "Filling application form (smart filler v3)...")
    time.sleep(4)
    handle_cookie_popup(driver, match)
    enable_translation_on_page(driver, match)
    time.sleep(2)

    # Wait for any form-like element to appear (React/Angular SPAs need time)
    FORM_HINTS = (
        "form,input[type='text'],input[type='email'],input[type='tel'],"
        "textarea,select,[role='combobox'],input[type='file'],"
        "input:not([type='hidden']):not([type='submit'])"
    )
    form_found = False
    for _wait in range(6):  # up to 6 extra seconds
        try:
            els = driver.find_elements(By.CSS_SELECTOR, FORM_HINTS)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                form_found = True
                log_event(match, "info",
                          f"Form elements detected ({len(visible)} visible) after {_wait}s extra wait")
                break
        except Exception:
            pass
        time.sleep(1)

    if not form_found:
        # Log what IS on the page for debugging
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text[:500].replace('\n', ' ')
            log_event(match, "warning",
                      f"No form elements found after 6s extra wait. "
                      f"Page text: {body_text[:200]}")
        except Exception:
            log_event(match, "warning", "No form elements found and page text unreadable")

    PHONE_LOCAL = extract_local_number(personal_info.get('phone', _defs['phone']), "+49")
    PHONE_FULL  = personal_info.get('phone', _defs['phone'])

    _company = (getattr(match, 'company_name', None) or '').strip()
    _title   = (getattr(match, 'job_title',   None) or '').strip()
    if not _company or _company.lower() in ('unknown company', 'company', ''):
        _company = 'your company'
    if not _title or _title.lower() in ('unknown', 'position', ''):
        _title = 'this position'

    COVER_TEXT = (
        f"Dear {_company} Team,\n\n"
        f"I am writing to express my sincere interest in the {_title} role at {_company}. "
        f"As a Computer Science student at Berlin School of Business and Innovation with "
        f"hands-on full-stack development experience using Python, Django and Flask, I am "
        f"confident I can contribute effectively from day one.\n\n"
        f"I am immediately available and fully authorised to work up to 20 hours per week "
        f"on my German student visa. I look forward to discussing how my skills can "
        f"support {_company}'s goals.\n\n"
        f"Kind regards,\n"
        f"{personal_info.get('name', _defs['name'])}"
    )

    FIELDS = {
        'first_name': {
            'patterns': ['first name','firstname','first-name','fname','given name',
                         'given-name','vorname','rufname','first_name','givenname'],
            'value': personal_info.get('first_name', _defs['first_name']),
            'exclude': ['last','email','phone','linkedin','url','company','middle'],
            'auto': False,
        },
        'last_name': {
            'patterns': ['last name','lastname','last-name','lname','surname',
                         'family name','family-name','nachname','familienname',
                         'last_name','familyname'],
            'value': personal_info.get('last_name', _defs['last_name']),
            'exclude': ['first','email','phone','linkedin','url','company','middle'],
            'auto': False,
        },
        'full_name': {
            'patterns': ['full name','fullname','full-name','your name','applicant name',
                         'candidate name','bewerbername','full_name','name'],
            'value': personal_info.get('name', _defs['name']),
            'exclude': ['email','phone','company','linkedin','url','middle'],
            'auto': False,
        },
        'email': {
            'patterns': ['email','e-mail','email address','email-address','mail',
                         'emailadresse','e-mail adresse','email_address'],
            'value': personal_info.get('email', _defs['email']),
            'exclude': ['phone','linkedin','company'],
            'auto': False,
        },
        'phone': {
            'patterns': ['phone','mobile','telephone','tel','cell','phone number',
                         'mobile number','contact number','handynummer','telefon',
                         'telefonnummer','mobilnummer','rufnummer','phone_number'],
            'value': PHONE_LOCAL,
            'value_full': PHONE_FULL,
            'exclude': ['email','fax','linkedin','website'],
            'auto': False,
        },
        'street_address': {
            'patterns': ['street','strasse','straße','street address','street-address',
                         'address line 1','address1','address line','line 1',
                         'street name','adresse','home address','street_address',
                         'street and number','strasse und hausnummer','anschrift'],
            'value': personal_info.get('street_address', _defs['street_address']),
            'exclude': ['email','phone','linkedin','url','company','postal','zip',
                        'city','country','website','portfolio','postcode'],
            'auto': False,
        },
        'postal_code': {
            'patterns': ['postal code','postal-code','postcode','post code','zip',
                         'zip code','zipcode','plz','postleitzahl','postal_code'],
            'value': personal_info.get('postal_code', _defs['postal_code']),
            'exclude': ['email','phone','street','linkedin','company','country',
                        'city','phone'],
            'auto': False,
        },
        'city': {
            'patterns': ['city','town','stadt','ort','wohnort'],
            'value': personal_info.get('city', 'Berlin'),
            'exclude': ['email','phone','street','postal','zip','country','company',
                        'linkedin','url','postcode'],
            'auto': False,
        },
        'country': {
            'patterns': ['country','country of residence','country of citizenship',
                         'land','nation','country_residence','country_of_residence'],
            'value': personal_info.get('country', 'Germany'),
            'select_alternatives': ['Germany','Deutschland','DE',
                                    'Federal Republic of Germany'],
            'exclude': ['email','phone','street','city','postal','zip','code'],
            'auto': False,
        },
        'location': {
            'patterns': ['location','current location','your location',
                         'residence','home city','standort'],
            'value': 'Berlin',
            'value_full': personal_info.get('location', 'Berlin, Germany'),
            'exclude': ['email','phone','linkedin','url','company','job','office',
                        'street','postal','zip'],
            'auto': True,
        },
        'linkedin': {
            'patterns': ['linkedin','linkedin url','linkedin-url','linkedin profile',
                         'linkedin_url','li profile'],
            'value': personal_info.get('linkedin', _defs['linkedin']),
            'exclude': ['email','phone','website','portfolio','github','twitter'],
            'auto': False,
        },
        'website': {
            'patterns': ['website','portfolio','personal website','personal site',
                         'github','portfolio url','webseite','portfolio_url'],
            'value': personal_info.get('linkedin', _defs['linkedin']),
            'exclude': ['linkedin','company','job'],
            'auto': False,
        },
        'visa': {
            'patterns': ['work authorization','work authorisation','authorized to work',
                         'authorised to work','right to work','visa','sponsorship required',
                         'require sponsorship','work permit','eligibility to work',
                         'arbeitserlaubnis','berechtigt zu arbeiten','work_authorization'],
            'value': 'Yes',
            'exclude': [],
            'auto': False,
        },
        'salary': {
            'patterns': ['salary','expected salary','desired salary','salary expectation',
                         'compensation','expected compensation','gehalt',
                         'gehaltsvorstellung','vergutung','salary_expectation'],
            'value': 'Negotiable',
            'exclude': ['notice','date'],
            'auto': False,
        },
        'notice_period': {
            'patterns': ['notice period','notice','available from','start date',
                         'earliest start','when can you start','availability',
                         'available to start','kundigungsfrist','verfugbar ab',
                         'startdatum','eintrittsdatum','notice_period'],
            'value': personal_info.get('notice_period', '1 month'),
            'exclude': ['salary','email','phone'],
            'auto': False,
        },
        'experience_years': {
            'patterns': ['years of experience','years experience','experience years',
                         'how many years','total experience','jahre erfahrung',
                         'berufserfahrung','years_experience'],
            'value': personal_info.get('experience_years', '1'),
            'exclude': ['salary','location','email'],
            'auto': False,
        },
        'cover_letter_text': {
            'patterns': ['cover letter','covering letter','motivation letter','motivation',
                         'why do you want','why are you interested','tell us about yourself',
                         'additional information','additional comments','message',
                         'personal statement','about yourself','anschreiben',
                         'motivationsschreiben','nachricht','weitere informationen',
                         'cover_letter','your message'],
            'value': COVER_TEXT,
            'exclude': ['email','phone','linkedin'],
            'auto': False,
        },
        'degree': {
            'patterns': ['highest education','education level','degree','highest degree',
                         'qualification','hochschulabschluss','abschluss','education_level'],
            'value': "Bachelor's",
            'select_alternatives': ["Bachelor's Degree","Bachelor","BSc","B.Sc.","Undergraduate"],
            'exclude': [],
            'auto': False,
        },
        'internship_type': {
            'patterns': ['type of internship','internship type','what type of internship',
                         'art des praktikums','praktikumsart','internship category',
                         'type of position','position type','employment type','job type',
                         'contract type','art der stelle','beschaeftigungsart','vertragsart'],
            'value': 'Internship',
            'select_alternatives': ['Working Student','Werkstudent','Part-time','Student',
                                    'Internship / Working Student','Trainee','Praktikum'],
            'exclude': [],
            'auto': False,
        },
        'hear_about_us': {
            'patterns': ['how did you hear','how did you find','where did you hear',
                         'how did you learn','referral source','source','wie haben sie',
                         'wie sind sie auf uns','woher kennen','channel',
                         'application source','how did you come across'],
            'value': 'LinkedIn',
            'select_alternatives': ['LinkedIn','Job Board','Online','Internet','Website','Other'],
            'exclude': ['email','phone'],
            'auto': False,
        },
        'gender': {
            'patterns': ['gender','sex','geschlecht','anrede','salutation','title',
                         'mr or ms','herr oder frau'],
            'value': 'Male',
            'select_alternatives': ['Mr','Herr','Man','männlich','male','M',
                                    'Prefer not to say','Not specified'],
            'exclude': ['email','phone','name','job','position'],
            'auto': False,
        },
        'nationality': {
            'patterns': ['nationality','citizenship','country of citizenship',
                         'staatsangehorigkeit','staatsburgerschaft','nationalitat'],
            'value': 'Indian',
            'select_alternatives': ['India','Indian','Other'],
            'exclude': ['email','phone'],
            'auto': False,
        },
        'current_company': {
            'patterns': ['current company','current employer','current organization',
                         'employer','company name','organization','organisation',
                         'aktueller arbeitgeber','unternehmen','firma',
                         'where do you work','place of work'],
            'value': 'Soften Technologies',
            'exclude': ['email','phone','linkedin','location'],
            'auto': False,
        },
        'current_position': {
            'patterns': ['current position','current title','current role','job title',
                         'current job title','present position','aktuelle position'],
            'value': 'Junior Python Full Stack Developer',
            'exclude': ['email','phone','company','linkedin'],
            'auto': False,
        },
        'why_company': {
            'patterns': ['why this company','why our company','why join us',
                         'why do you want to work','what attracts you','interest in our company',
                         'warum unser unternehmen','warum bei uns'],
            'value': (
                'I am drawn to your engineering culture, real product impact, and the '
                'opportunity to grow alongside experienced developers in a collaborative '
                'environment that values quality, learning and ownership.'
            ),
            'exclude': ['email','phone','linkedin','salary'],
            'auto': False,
        },
        'why_role': {
            'patterns': ['why this role','why this position','why are you a good fit',
                         'what makes you a good fit','tell us why','warum diese position',
                         'why are you interested','what excites you about this role'],
            'value': (
                'My hands-on Python/Django experience, focus on secure backend design and '
                'proven track record of shipping production features make this role a '
                'strong match for both my skills and learning goals.'
            ),
            'exclude': ['email','phone','linkedin','salary'],
            'auto': False,
        },
        'work_authorization_country': {
            'patterns': ['authorized to work in','authorised to work in',
                         'work authorization country','country of work authorization',
                         'right to work in','eligible to work in'],
            'value': 'Germany',
            'select_alternatives': ['Germany','Deutschland','DE','Yes - Germany',
                                    'Yes, Germany'],
            'exclude': [],
            'auto': False,
        },
        'start_date': {
            'patterns': ['start date','earliest start date','available start date',
                         'when can you start','available from','desired start date',
                         'startdatum','eintrittsdatum','fruhester eintritt'],
            'value': 'Immediately',
            'select_alternatives': ['Immediately','ASAP','As soon as possible',
                                    'Within 1 month','Sofort','Innerhalb von 1 Monat'],
            'exclude': ['salary','email','phone'],
            'auto': False,
        },
        'references': {
            'patterns': ['references','referees','reference name','reference contact',
                         'referenzen','referenz'],
            'value': 'Available upon request',
            'exclude': ['email','phone'],
            'auto': False,
        },
        'language_proficiency': {
            'patterns': ['english level','english proficiency','language level',
                         'language proficiency','language skill','sprachniveau',
                         'sprachkenntnisse','englischkenntnisse','german level',
                         'deutsch kenntnisse'],
            'value': 'C1 - Advanced',
            'select_alternatives': ['C1','Advanced','B2','Upper Intermediate',
                                    'Fluent','Professional','C1 Advanced'],
            'exclude': [],
            'auto': False,
        },
        'disability': {
            'patterns': ['disability','disabled','behinderung','schwerbehinderung',
                         'severely disabled','handicap','behinderungsgrad'],
            'value': 'No',
            'select_alternatives': ['No','Nein','None','Not applicable','N/A'],
            'exclude': [],
            'auto': False,
        },
    }

    filled_set   = set()
    total_filled = 0

    def run_one_step():
        nonlocal total_filled
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        select_country_code(driver, match, "+49")
        total_filled += _process_frame_fields(
            driver, match, FIELDS, filled_set, PHONE_LOCAL, PHONE_FULL)
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            iframes = []
        for idx in range(len(iframes)):
            try:
                fresh_iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if idx >= len(fresh_iframes):
                    break
                driver.switch_to.default_content()
                driver.switch_to.frame(fresh_iframes[idx])
                time.sleep(0.3)
                total_filled += _process_frame_fields(
                    driver, match, FIELDS, filled_set, PHONE_LOCAL, PHONE_FULL)
            except Exception:
                pass
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

    UP = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    LO = 'abcdefghijklmnopqrstuvwxyz'
    cv_uploaded = False

    for step in range(8):
        log_event(match, "info", f"Form step {step + 1}/8")
        handle_cookie_popup(driver, match)
        if not cv_uploaded:
            if _upload_cv(driver, cv_path, match):
                cv_uploaded = True
        run_one_step()

        # Retry logic: if step 1 found 0 fields, wait longer and re-scan
        # (React/Angular SPAs sometimes need 8-10s to fully render forms)
        if step == 0 and total_filled == 0:
            log_event(match, "info", "0 fields on first scan — retrying after 5s extra wait")
            time.sleep(5)
            handle_cookie_popup(driver, match)
            run_one_step()

        clicked_next = False
        for nt in ['next', 'continue', 'weiter', 'fortfahren', 'next step', 'proceed',
                   'naechster schritt', 'save and continue']:
            if clicked_next:
                break
            try:
                xpath = (
                    f"//button[translate(normalize-space(.),'{UP}','{LO}')='{nt}']"
                    f"|//input[@type='submit' and "
                    f"translate(normalize-space(@value),'{UP}','{LO}')='{nt}']"
                )
                for btn in driver.find_elements(By.XPATH, xpath):
                    if not btn.is_displayed() or not btn.is_enabled():
                        continue
                    if 'disabled' in (btn.get_attribute('class') or ''):
                        continue
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.4)
                    try:
                        btn.click()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", btn)
                    time.sleep(3)
                    handle_cookie_popup(driver, match)
                    log_event(match, "info", f"Clicked Next: {btn.text[:30]}")
                    clicked_next = True
                    break
            except Exception:
                continue

        if not clicked_next:
            log_event(match, "info", "No Next button — form fill complete")
            break

    log_event(match, "success", f"Form fill done — {total_filled} fields filled")

    # Diagnostic: when 0 fields filled, log visible page elements for debugging
    if total_filled == 0:
        try:
            url = driver.current_url
            title_tag = driver.title
            # Count visible inputs, buttons, links
            inputs = len([e for e in driver.find_elements(By.CSS_SELECTOR,
                "input,textarea,select") if e.is_displayed()])
            buttons = []
            for b in driver.find_elements(By.CSS_SELECTOR, "button,a.btn,[role='button']")[:10]:
                try:
                    if b.is_displayed():
                        t = (b.text or '').strip()[:40]
                        if t:
                            buttons.append(t)
                except Exception:
                    continue
            log_event(match, "warning",
                      f"0-field diagnostic: url={url[:80]} title={title_tag[:50]} "
                      f"visible_inputs={inputs} buttons={buttons[:6]}")
        except Exception:
            pass
    return total_filled > 0


# =============================================================================
# SUBMIT APPLICATION
# =============================================================================

def submit_application(driver, match):
    try:
        log_event(match, "action", "Submitting application...")
        time.sleep(2)
        handle_cookie_popup(driver, match)

        UP = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        LO = 'abcdefghijklmnopqrstuvwxyz'

        SUBMIT = ['submit','submit application','send application','send','finish',
                  'complete','absenden','bewerbung absenden','abschicken','einreichen',
                  'send my application','complete application','submit my application']
        NEXT   = ['next','continue','review','proceed','weiter','fortfahren','save and continue']
        # CRITICAL: never click these — they are not real submit buttons
        BAD    = ['save','cancel','close','back','speichern','abbrechen','zuruck',
                  'apply with linkedin','sign in','sign-in','log in','login','log-in',
                  'register','sign up','signup','create account','create an account',
                  'connect with linkedin','continue with linkedin','continue with google',
                  'apply with indeed','apply with xing','mit linkedin bewerben',
                  'mit linkedin anmelden','anmelden','einloggen','registrieren',
                  'create profile','profil erstellen','passwort','password',
                  'forgot password','passwort vergessen',
                  # — "apply later" / "send me the link" variants
                  'apply later','save for later','remind me','request link',
                  'send link','send me','link anfordern','link zusenden',
                  'link per email','link per e-mail','per mail','per e-mail',
                  'erinnern','erinnerung','merkliste','apply on mobile',
                  'später bewerben','spaeter bewerben','bewerbung speichern']

        def _is_bad(text_lower):
            return any(b in text_lower for b in BAD)

        for txt in SUBMIT + NEXT:
            xpath = (
                f"//button[translate(normalize-space(.),'{UP}','{LO}')='{txt}']"
                f"|//input[@type='submit' and "
                f"translate(normalize-space(@value),'{UP}','{LO}')='{txt}']"
            )
            for btn in driver.find_elements(By.XPATH, xpath):
                try:
                    if not btn.is_displayed() or not btn.is_enabled():
                        continue
                    bl = (btn.text or btn.get_attribute('value') or '').lower()
                    aria_l = (btn.get_attribute('aria-label') or '').lower()
                    combined = f"{bl} {aria_l}"
                    if _is_bad(combined) or \
                            'disabled' in (btn.get_attribute('class') or ''):
                        continue
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    try:
                        btn.click()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", btn)
                    time.sleep(4)
                    handle_cookie_popup(driver, match)
                    log_event(match, "info", f"Clicked: {bl[:40]}")
                    return 'submitted' if txt in SUBMIT else 'next'
                except Exception:
                    continue

        for btn in driver.find_elements(By.CSS_SELECTOR,
                "button.artdeco-button--primary, button[type='submit'], "
                "input[type='submit']"):
            try:
                if not btn.is_displayed() or not btn.is_enabled():
                    continue
                bl = (btn.text or btn.get_attribute('value') or '').lower()
                aria_l = (btn.get_attribute('aria-label') or '').lower()
                combined = f"{bl} {aria_l}"
                if _is_bad(combined):
                    log_event(match, "info", f"Skipped sketchy button: {bl[:40]}")
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.5)
                try:
                    btn.click()
                except ElementClickInterceptedException:
                    driver.execute_script("arguments[0].click();", btn)
                time.sleep(4)
                log_event(match, "info", f"Primary button: {bl[:40]}")
                return 'submitted'
            except Exception:
                continue

        return None
    except Exception as e:
        log_event(match, "error", f"Submit error: {e}")
        return None


# =============================================================================
# JOB SEARCH
# =============================================================================

def search_jobs_senior(job_query, max_results=10):
    if not SELENIUM_AVAILABLE:
        print("[ERROR] Selenium not available")
        return []
    driver = None
    jobs_found = []
    try:
        driver = create_chrome_driver()
        kw  = job_query.title.replace(' ', '%20')
        loc = (job_query.location or '').replace(' ', '%20')
        url = f"https://www.linkedin.com/jobs/search/?keywords={kw}"
        if loc:
            url += f"&location={loc}"
        url += "&f_TPR=r86400&sortBy=DD"
        driver.get(url)
        time.sleep(5)
        for idx, card in enumerate(get_job_cards_from_page(driver)[:max_results]):
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
                time.sleep(0.5)
                card.click(); time.sleep(2)
                d = get_job_details_from_page(driver, None)
                if d and d['title'] != "Unknown Position":
                    jobs_found.append(d)
                    AutomatedJobMatch.objects.update_or_create(
                        job_query=job_query, job_url=d['url'],
                        defaults={'company_name': d['company'],
                                  'job_title': d['title'],
                                  'status': 'ready', 'ats_score': 0}
                    )
            except Exception as e:
                print(f"[WARNING] card {idx}: {e}")
                continue
        return jobs_found
    except Exception as e:
        print(f"[ERROR] search_jobs_senior: {e}")
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# =============================================================================
# MAIN AUTOMATION
# =============================================================================

def run_job_application_automation(match_id, quick_apply=False):
    try:
        match = AutomatedJobMatch.objects.select_related(
            'job_query', 'job_query__user').get(id=match_id)
    except Exception as e:
        print(f"Failed to load match {match_id}: {e}")
        return

    log_event(match, "info", "=" * 60)
    log_event(match, "info", "STARTING JOB APPLICATION AUTOMATION")
    log_event(match, "info", "=" * 60)

    config = JobApplicationConfig()
    config.job_role  = match.job_query.title if match.job_query else "Python Developer"
    config.location  = (getattr(match.job_query, 'location', None) or 'Berlin, Germany')

    search_kw_str = ''
    if match.job_query:
        search_kw_str = (getattr(match.job_query, 'search_keywords', '') or '').strip()

    config.keywords = [
        k.strip().lower()
        for k in re.split(r'[,;\n]+', search_kw_str)
        if k.strip() and len(k.strip()) > 1
    ]

    STOP = {'the','for','and','with','your','our','job','position','a','an',
            'of','in','on','at','to','or','is','as','be','by'}
    if match.job_query and match.job_query.title:
        for w in re.split(r'[\s\-/&]+', match.job_query.title):
            wl = w.lower().strip()
            if len(wl) > 2 and wl not in STOP and wl not in config.keywords:
                config.keywords.append(wl)

    if not config.keywords:
        log_event(match, "warning", "No title words to match — using broad defaults")
        config.keywords = ['developer', 'engineer']

    log_event(match, "info",
              f"Title-derived match keywords: {config.keywords} "
              f"(JD keywords extracted per job during tailoring)")
    config.max_applications = 10

    user_cv_text = ""
    if match.job_query and match.job_query.base_cv:
        try:
            user_cv_text = get_resume_text(match.job_query.base_cv.path)
            log_event(match, "success", "User CV loaded")
        except Exception as e:
            log_event(match, "warning", f"Could not load CV: {e}")

    base_sections = extract_cv_sections(user_cv_text)
    personal_info = extract_personal_info(user_cv_text)
    _defs = _applicant_defaults()

    if not SELENIUM_AVAILABLE:
        log_event(match, "error", "Selenium not installed")
        match.status = 'failed'; match.save()
        return

    driver         = None
    apps_submitted = 0
    processed      = set()

    RESULTS_WAIT_SELECTORS = (
        "ul.jobs-search-results__list,"
        "div.jobs-search-results-list,"
        "div.scaffold-layout__list,"
        "div[class*='jobs-search-results'],"
        "li.jobs-search-results__list-item,"
        "div[data-occludable-job-id],"
        "li[data-occludable-entity-urn]"
    )

    try:
        driver = create_chrome_driver()
        log_event(match, "success", "Browser launched")

        if not linkedin_login(driver, match, config):
            match.status = 'failed'; match.save()
            return

        kw_enc  = config.job_role.replace(' ', '%20')
        loc_enc = config.location.replace(' ', '%20')
        search_url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={kw_enc}&location={loc_enc}"
            f"&f_TPR=r86400&sortBy=DD"
        )
        log_event(match, "info", f"Search URL (last 24h): {search_url}")

        MAX_ITER = 20
        iters    = 0

        while apps_submitted < config.max_applications:
            iters += 1
            if iters > MAX_ITER:
                log_event(match, "warning", f"Max iterations ({MAX_ITER}) reached — stopping")
                break

            try:
                driver.get(search_url)
                time.sleep(6)
                handle_cookie_popup(driver, match)
                enable_translation_on_page(driver, match)

                try:
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, RESULTS_WAIT_SELECTORS))
                    )
                    log_event(match, "info", "Results container found")
                except TimeoutException:
                    log_event(match, "warning", "Results container not found after 20s")
                    try:
                        src = driver.page_source[:600].replace('\n', ' ')
                        log_event(match, "warning", f"Page snapshot: {src}")
                    except Exception:
                        pass

                time.sleep(3)

                cards = get_job_cards_from_page(driver, match)
                if not cards:
                    log_event(match, "warning", "No cards found, retrying...")
                    time.sleep(8)
                    continue

                log_event(match, "info", f"Found {len(cards)} jobs")
                job_processed = False

                for idx in range(len(cards)):
                    if apps_submitted >= config.max_applications:
                        break

                    ok, msg = click_job_card_safely(driver, idx, match)
                    if not ok:
                        log_event(match, "warning", f"Card {idx+1}: {msg}")
                        continue

                    handle_cookie_popup(driver, match)
                    enable_translation_on_page(driver, match)

                    details = get_job_details_from_page(driver, match)
                    if not details:
                        continue

                    company     = details.get('company', 'Unknown')
                    title       = details.get('title',   'Unknown')
                    job_url     = details.get('url',     driver.current_url)
                    description = details.get('description', '')
                    job_id      = f"{company}_{title}".lower().replace(' ', '_')

                    if job_id in processed or is_job_applied(job_url, company, title):
                        processed.add(job_id)
                        continue

                    job_text   = (title + " " + description).lower()
                    matched_kw = [k for k in config.keywords if k.lower() in job_text]
                    if not matched_kw:
                        processed.add(job_id); continue

                    log_event(match, "success", f"Match: {title} at {company}")

                    if details.get('is_easy_apply') and not details.get('has_apply_button'):
                        log_event(match, "info", "Easy Apply only — skipping")
                        processed.add(job_id); continue

                    ats_score = calculate_ats_score(
                        description, title, matched_kw, config.keywords)
                    cv_sections, extracted_kw = build_tailored_cv(
                        base_sections, title, company, description, config.keywords)

                    top_kw = [k for k in extracted_kw
                              if k.lower() not in {'rest', 'sql', 'ai', 'api'}][:3]
                    if not top_kw:
                        top_kw = extracted_kw[:3] if extracted_kw else \
                                 ['Python', 'Django', 'REST APIs']
                    tagline_parts = [
                        k.upper() if len(k) <= 3 else k.title() for k in top_kw
                    ]
                    clean_role = _clean_role_title(title)
                    personal_info['tagline'] = (
                        f"{clean_role} | {' | '.join(tagline_parts)} | Berlin"
                    )

                    cover_letter = generate_tailored_cover_letter(
                        personal_info, title, company, description, extracted_kw)

                    cv_buffer = create_cv_pdf(cv_sections, personal_info)
                    cl_buffer = create_cover_letter_pdf(
                        cover_letter, personal_info, company, title)

                    if not cv_buffer or not cl_buffer:
                        log_event(match, "error", "Failed to generate PDFs")
                        continue

                    log_event(match, "success", f"Documents ready (ATS: {ats_score}%)")

                    applicant_clean = re.sub(
                        r'\W+', '', personal_info.get('name', _defs['name']))
                    upload_dir = os.path.join(
                        tempfile.gettempdir(),
                        f"app_{match.id}_{apps_submitted}")
                    os.makedirs(upload_dir, exist_ok=True)
                    cv_path = os.path.join(upload_dir, f"{applicant_clean}.pdf")
                    cv_buffer.seek(0)
                    with open(cv_path, 'wb') as f:
                        f.write(cv_buffer.read())
                    cv_buffer.seek(0)

                    clicked, click_msg = click_external_apply(driver, match)
                    if not clicked:
                        log_event(match, "info", f"Skipping: {click_msg}")
                        processed.add(job_id)
                        try:
                            os.remove(cv_path)
                            os.rmdir(os.path.dirname(cv_path))
                        except Exception:
                            pass
                        continue

                    if len(driver.window_handles) > 1:
                        driver.switch_to.window(driver.window_handles[-1])
                        time.sleep(3)
                        handle_cookie_popup(driver, match)
                        enable_translation_on_page(driver, match)

                    match.company_name      = company
                    match.job_title         = title
                    match.job_url           = job_url
                    match.tailored_cv_text  = json.dumps(cv_sections, default=str)
                    match.cover_letter_text = cover_letter
                    match.ats_score         = ats_score
                    match.save()

                    fields_filled = fill_application_form(
                        driver, match, personal_info, cv_path, extracted_kw)

                    # Detect login wall — external sites that require account creation
                    try:
                        page_text_lower = driver.find_element(
                            By.TAG_NAME, "body").text.lower()[:3000]
                        login_wall_phrases = [
                            # English
                            'apply with linkedin', 'sign in to apply',
                            'log in to apply', 'create an account to apply',
                            'register to apply', 'sign up to apply',
                            'please sign in', 'please log in',
                            'continue with linkedin', 'continue with google',
                            'sign in to continue', 'log in to continue',
                            'create account', 'create your account',
                            'sign up for free', 'register for free',
                            'already have an account', 'don\'t have an account',
                            'sign in with google', 'sign in with email',
                            'connect with linkedin', 'apply with indeed',
                            'sign in or create', 'login or register',
                            'join now to apply', 'join to apply',
                            # German
                            'mit linkedin bewerben', 'mit linkedin anmelden',
                            'anmelden um zu bewerben', 'konto erstellen',
                            'registrieren sie sich', 'bitte melden sie sich an',
                            'einloggen um fortzufahren', 'jetzt registrieren',
                            'mit google anmelden', 'mit google fortfahren',
                            'anmelden oder registrieren', 'konto anlegen',
                            'bereits ein konto', 'noch kein konto',
                        ]
                        is_login_wall = (
                            not fields_filled and
                            any(p in page_text_lower for p in login_wall_phrases)
                        )
                    except Exception:
                        is_login_wall = False

                    if is_login_wall:
                        log_event(match, "info",
                                  "Login wall detected — skipping (no real form)")
                        processed.add(job_id)
                        try:
                            os.remove(cv_path)
                            os.rmdir(os.path.dirname(cv_path))
                        except Exception:
                            pass
                        while len(driver.window_handles) > 1:
                            driver.switch_to.window(driver.window_handles[-1])
                            driver.close()
                            driver.switch_to.window(driver.window_handles[0])
                        continue

                    try:
                        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                        if any(w in page_text for w in
                               ['verify email', 'check email',
                                'confirmation sent', 'verify your email']):
                            link = check_email_for_verification(match, max_wait=180)
                            if link:
                                handle_email_verification(driver, match, link)
                    except Exception:
                        pass

                    result = submit_application(driver, match)

                    # Only count as a REAL submission if we actually filled fields.
                    # Otherwise the "submit" click was probably a misclick on a
                    # login button, social-sign-in, or similar non-form button.
                    if result in ('submitted', 'next') and fields_filled:
                        apps_submitted += 1
                        mark_job_applied(job_url, company, title)
                        processed.add(job_id)
                        cv_buffer.seek(0); cl_buffer.seek(0)
                        send_application_email(
                            match, personal_info, cv_buffer, cl_buffer,
                            company, title, job_url, ats_score)
                        log_event(match, "success",
                                  f"APPLICATION #{apps_submitted} COMPLETE!")
                        job_processed = True
                        break
                    elif result in ('submitted', 'next') and not fields_filled:
                        log_event(match, "warning",
                                  "Submit fired but 0 fields filled — likely "
                                  "false positive, NOT counted as applied")
                        processed.add(job_id)

                    try:
                        os.remove(cv_path)
                        os.rmdir(os.path.dirname(cv_path))
                    except Exception:
                        pass

                    while len(driver.window_handles) > 1:
                        driver.switch_to.window(driver.window_handles[-1])
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])
                    processed.add(job_id)

                if not job_processed:
                    log_event(match, "warning", "No jobs processed this iteration")
                    time.sleep(3)

            except InvalidSessionIdException:
                log_event(match, "error", "Browser session lost. Recreating driver...")
                try:
                    if driver:
                        try: driver.quit()
                        except Exception: pass
                    time.sleep(3)
                    driver = create_chrome_driver()
                    log_event(match, "info", "Driver recreated successfully")
                    if not linkedin_login(driver, match, config):
                        log_event(match, "error", "Re-login failed after crash")
                        break
                    log_event(match, "success", "Re-logged in after crash")
                except Exception as recreate_err:
                    log_event(match, "error", f"Failed to recreate driver: {recreate_err}")
                    break
            except Exception as e:
                log_event(match, "error", f"Loop error: {e}")
                try:
                    while len(driver.window_handles) > 1:
                        driver.switch_to.window(driver.window_handles[-1])
                        driver.close()
                    driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass
                time.sleep(5)
                continue

        log_event(match, "info", "=" * 60)
        log_event(match, "info", "AUTOMATION COMPLETE")
        log_event(match, "info", "=" * 60)

        if apps_submitted > 0:
            match.status     = 'applied'
            match.applied_at = timezone.now()
            log_event(match, "success", f"Submitted {apps_submitted} applications!")
        else:
            match.status = 'failed'
            log_event(match, "warning", "No applications submitted")
        match.save()

    except Exception as e:
        log_event(match, "error", f"Critical error: {e}")
        import traceback; traceback.print_exc()
        match.status = 'failed'; match.save()
    finally:
        if driver:
            log_event(match, "info", "Closing browser...")
            time.sleep(5)
            try:
                driver.quit()
            except Exception:
                pass


# =============================================================================
# LEGACY / ALIAS FUNCTIONS
# =============================================================================

def tailor_resume_logic(job):
    if not job.base_cv:
        return False
    try:
        AutomatedJobMatch.objects.update_or_create(
            job_query=job,
            defaults={
                'company_name': job.platform, 'job_title': job.title,
                'tailored_cv_text': 'Generated',
                'cover_letter_text': 'Generated',
                'ats_score': 90, 'status': 'ready',
            }
        )
        return True
    except Exception as e:
        print(f"tailor_resume_logic error: {e}")
        return False


def generate_tailored_pdf(match, personal_info=None, doc_type='combined'):
    try:
        pi = personal_info or extract_personal_info("")
        base = extract_cv_sections("")
        jt = match.job_title or "Position"
        co = match.company_name or "Company"
        cv_sec, _ = build_tailored_cv(base, jt, co, "", [])
        if doc_type == 'cv':
            return create_cv_pdf(cv_sec, pi)
        elif doc_type == 'cover_letter':
            cl = generate_tailored_cover_letter(pi, jt, co, "", [])
            return create_cover_letter_pdf(cl, pi, co, jt)
        else:
            cv_buf = create_cv_pdf(cv_sec, pi)
            cl     = generate_tailored_cover_letter(pi, jt, co, "", [])
            cl_buf = create_cover_letter_pdf(cl, pi, co, jt)
            if cv_buf and cl_buf:
                if PDFWRITER_AVAILABLE:
                    writer = PdfWriter()
                    cv_buf.seek(0); cl_buf.seek(0)
                    for page in PdfReader(cv_buf).pages:
                        writer.add_page(page)
                    for page in PdfReader(cl_buf).pages:
                        writer.add_page(page)
                    out = BytesIO(); writer.write(out); out.seek(0)
                    return out
                else:
                    merger = PdfMerger()
                    merger.append(cv_buf); merger.append(cl_buf)
                    out = BytesIO()
                    merger.write(out); merger.close()
                    out.seek(0)
                    return out
            return cv_buf or cl_buf
    except Exception as e:
        print(f"generate_tailored_pdf error: {e}")
        return None


def generate_cover_letter_pdf(match, personal_info=None):
    try:
        pi = personal_info or extract_personal_info("")
        cl = generate_tailored_cover_letter(
            pi, match.job_title or "Position",
            match.company_name or "Company", "", [])
        return create_cover_letter_pdf(cl, pi, match.company_name, match.job_title)
    except Exception as e:
        print(f"generate_cover_letter_pdf error: {e}")
        return None


def send_application_email_senior(match, personal_info, cv_pdf_path, cl_pdf_path):
    try:
        cv_buf = cl_buf = None
        if cv_pdf_path and os.path.exists(cv_pdf_path):
            with open(cv_pdf_path, 'rb') as f:
                cv_buf = BytesIO(f.read())
        if cl_pdf_path and os.path.exists(cl_pdf_path):
            with open(cl_pdf_path, 'rb') as f:
                cl_buf = BytesIO(f.read())
        return send_application_email(
            match, personal_info, cv_buf, cl_buf,
            match.company_name, match.job_title,
            getattr(match, 'job_url', ''), 90)
    except Exception as e:
        print(f"send_application_email_senior error: {e}")
        return False


def click_apply_button_safe(driver, match):
    ok, msg = click_external_apply(driver, match)
    if ok:
        log_event(match, "success", "Apply button clicked")
    else:
        log_event(match, "warning", f"Apply button not found: {msg}")
    return ok


def generate_tailored_cv_senior(base_sections=None, job_title="Position",
                                 company_name="Company", job_description="",
                                 job_keywords=None):
    if base_sections is None:
        base_sections = extract_cv_sections("")
    if job_keywords is None:
        job_keywords = []
    cv_sections, all_keywords = build_tailored_cv(
        base_sections, job_title, company_name, job_description, job_keywords)
    return cv_sections, 90, all_keywords


# Aliases
generate_cover_letter_senior = generate_tailored_cover_letter
create_cv_pdf_senior         = create_cv_pdf
fill_application_form_senior = fill_application_form
submit_application_senior    = submit_application
run_browser_automation       = run_job_application_automation