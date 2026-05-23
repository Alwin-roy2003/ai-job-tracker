# AI Job Hunter Tool

A browser automation tool built to learn and demonstrate **Selenium WebDriver**, **LLM API integration**, and **full-stack Django development**. The tool automates job searching on LinkedIn, generates tailored CVs and cover letters using AI, and fills external application forms.

## What This Project Demonstrates

- **Selenium WebDriver automation** — login flows, dynamic element detection, cookie popup handling, iframe traversal, stale element recovery, and anti-bot evasion techniques
- **LLM integration** — Google Gemini API for generating tailored cover letters with structured prompts and fallback templates
- **PDF generation** — dynamic CV and cover letter creation using ReportLab with ATS-optimized formatting
- **Smart form filling** — field detection via CSS/XPath selectors, label matching with scoring system, support for native and custom dropdowns (React-select, Workday, Greenhouse), autocomplete handling, and file upload
- **Error resilience** — Chrome crash recovery with automatic driver restart, login wall detection, false-positive submission prevention, and multi-step retry logic
- **Django REST backend** — job query management, application logging, and status tracking via REST API

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| Backend | Python, Django, Django REST Framework |
| Automation | Selenium WebDriver, ChromeDriver, WebDriver Manager |
| AI / LLM | Google Gemini API (cover letter generation) |
| PDF | ReportLab (CV + cover letter PDF rendering) |
| Database | PostgreSQL |
| Email | SMTP (Gmail) with PDF attachments |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/Alwin-roy2003/ai-job-tracker.git
cd ai-job-tracker
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

### 3. Database setup

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Configure LinkedIn credentials

Add your LinkedIn credentials via the Django admin panel at `/admin/` under **PlatformConfig**.

### 5. Run

```bash
python manage.py runserver
```

## How It Works

1. **Login** — authenticates with LinkedIn using Selenium, handling CAPTCHAs and cookie popups
2. **Search** — queries LinkedIn Jobs with keyword + location filters (last 24 hours, sorted by date)
3. **Match** — scans job cards, extracts title/company/description, matches against configured keywords
4. **Skip Easy Apply** — filters out Easy Apply jobs (targets external ATS applications only)
5. **Tailor documents** — generates a keyword-optimized CV and Gemini-powered cover letter for each job
6. **Apply** — navigates to the external application page, fills form fields intelligently, uploads CV, and submits
7. **Notify** — sends an email with the tailored CV and cover letter as PDF attachments

## Key Engineering Decisions

- **Field scoring system** — each form field is scored against 25+ field type patterns using ID, name, label, aria-label, and placeholder attributes. Highest-scoring match wins.
- **Multi-strategy form filling** — tries React-style value injection, DOM manipulation, character-by-character input, and execCommand in sequence until one succeeds.
- **Login wall detection** — identifies 34 phrases (English + German) that indicate an account is required, and skips those sites instead of submitting empty forms.
- **False-positive prevention** — submissions are only counted when form fields were actually filled, preventing misclicks on social login buttons from inflating the count.

## Disclaimer

This project was built as a **learning exercise** for Selenium browser automation and LLM integration. It is not intended for production use.

## License

MIT
