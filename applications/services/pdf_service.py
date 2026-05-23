"""
PDF Service Module - Professional CV/Resume PDF Generation
"""

from io import BytesIO
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, mm
from reportlab.lib.colors import HexColor, black


@dataclass
class CVSection:
    """Represents a CV section"""
    title: str
    content: List[str]
    style: str = "default"  # default, bullet, two_column, date_line


class CVTemplateRenderer:
    """
    Professional CV template renderer - Matches Alwin's CV template exactly
    Clean, modern two-column layout with professional styling
    """
    
    # Page setup
    PAGE_SIZE = A4
    MARGIN_LEFT = 20 * mm
    MARGIN_RIGHT = 20 * mm
    MARGIN_TOP = 15 * mm
    
    # Colors - professional dark theme
    COLOR_HEADER_BG = HexColor('#2c3e50')  # Dark blue-gray header
    COLOR_TEXT_DARK = HexColor('#2c3e50')   # Dark text for headings
    COLOR_TEXT_NORMAL = HexColor('#333333') # Normal text
    COLOR_ACCENT = HexColor('#34495e')      # Slightly lighter for accents
    
    # Fonts
    FONT_NAME = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"
    
    def __init__(self, personal_info: Dict, sections: Dict[str, str]):
        """
        Initialize renderer with personal info and section content
        
        Args:
            personal_info: Dict with name, email, phone, linkedin, location, 
                          visa_status, availability, languages
            sections: Dict with section names as keys, content strings as values
        """
        self.personal_info = personal_info
        self.sections = self._parse_sections(sections)
        self.buffer = BytesIO()
        self.canvas = canvas.Canvas(self.buffer, pagesize=self.PAGE_SIZE)
        self.width, self.height = self.PAGE_SIZE
        self.y = self.height - self.MARGIN_TOP
        
    def _parse_sections(self, raw_sections: Dict[str, str]) -> List[CVSection]:
        """Parse AI-generated text into structured sections"""
        parsed = []
        
        # Define section order to match your CV exactly
        section_order = [
            ('PROFILE', 'PROFILE'),
            ('PROFESSIONAL SUMMARY', 'PROFILE'),
            ('SUMMARY', 'PROFILE'),
            ('WORK EXPERIENCE', 'WORK EXPERIENCE'),
            ('PROFESSIONAL EXPERIENCE', 'WORK EXPERIENCE'),
            ('EXPERIENCE', 'WORK EXPERIENCE'),
            ('PROJECTS', 'PROJECTS'),
            ('EDUCATION', 'EDUCATION'),
            ('TECHNICAL SKILLS', 'TECHNICAL SKILLS'),
            ('SKILLS', 'TECHNICAL SKILLS'),
            ('LANGUAGES', 'LANGUAGES'),
            ('INTERESTS', 'INTERESTS'),
            ('INTEREST', 'INTERESTS'),
        ]
        
        processed = set()
        for key, display_name in section_order:
            # Check various possible keys
            actual_key = None
            for k in [key, key.upper(), key.lower(), key.title()]:
                if k in raw_sections:
                    actual_key = k
                    break
            
            if actual_key and display_name not in processed:
                content = raw_sections[actual_key]
                
                # Determine style based on section type
                if display_name == 'TECHNICAL SKILLS':
                    style = 'two_column'
                elif display_name in ['WORK EXPERIENCE', 'PROJECTS', 'EDUCATION']:
                    style = 'experience'
                else:
                    style = 'paragraph'
                
                # Clean and split content
                lines = [line.strip() for line in content.split('\n') if line.strip()]
                
                parsed.append(CVSection(
                    title=display_name,
                    content=lines,
                    style=style
                ))
                processed.add(display_name)
        
        return parsed
    
    def render(self) -> BytesIO:
        """
        Main render method - generates the PDF with two-column layout
        """
        self._render_header()
        self._render_sidebar()
        self._render_main_content()
        
        self.canvas.save()
        self.buffer.seek(0)
        return self.buffer
    
    def _render_header(self):
        """Render dark header bar with name"""
        header_height = 25 * mm
        
        # Draw header background
        self.canvas.setFillColor(self.COLOR_HEADER_BG)
        self.canvas.rect(0, self.height - header_height, self.width, header_height, 
                        fill=1, stroke=0)
        
        # Name in white, large, bold
        self.canvas.setFillColor(HexColor('#ffffff'))
        self.canvas.setFont(self.FONT_BOLD, 22)
        name = self.personal_info.get('name', 'CANDIDATE NAME')
        self.canvas.drawString(self.MARGIN_LEFT, self.height - 17 * mm, name)
        
        self.y = self.height - header_height - 8 * mm
    
    def _render_sidebar(self):
        """Render left sidebar with personal details"""
        sidebar_width = 60 * mm
        sidebar_x = self.MARGIN_LEFT
        content_start_y = self.y
        
        # Personal Details section
        self._render_sidebar_section(sidebar_x, "PERSONAL DETAILS", content_start_y)
        current_y = content_start_y - 8 * mm
        
        self.canvas.setFont(self.FONT_NAME, 9)
        self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
        
        # Detail items
        details = [
            ('Residence Status', self.personal_info.get('visa_status', '')),
            ('Availability', self.personal_info.get('availability', '')),
            ('Languages', self.personal_info.get('languages', '')),
        ]
        
        for label, value in details:
            if value:
                self.canvas.setFont(self.FONT_BOLD, 8)
                self.canvas.setFillColor(self.COLOR_TEXT_DARK)
                self.canvas.drawString(sidebar_x, current_y, label)
                current_y -= 4 * mm
                
                self.canvas.setFont(self.FONT_NAME, 9)
                self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
                # Wrap long text
                wrapped = self._wrap_text(value, 25)
                for line in wrapped:
                    self.canvas.drawString(sidebar_x, current_y, line)
                    current_y -= 4 * mm
                current_y -= 2 * mm
        
        # Contact info in sidebar
        current_y -= 4 * mm
        self._render_sidebar_section(sidebar_x, "CONTACT", current_y)
        current_y -= 8 * mm
        
        contact_items = [
            self.personal_info.get('email', ''),
            self.personal_info.get('phone', ''),
            self.personal_info.get('location', ''),
        ]
        
        self.canvas.setFont(self.FONT_NAME, 9)
        self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
        for item in contact_items:
            if item:
                wrapped = self._wrap_text(item, 28)
                for line in wrapped:
                    self.canvas.drawString(sidebar_x, current_y, line)
                    current_y -= 4 * mm
                current_y -= 1 * mm
        
        # Store sidebar bottom for reference
        self.sidebar_bottom = current_y
    
    def _render_sidebar_section(self, x, title, y):
        """Render sidebar section header"""
        self.canvas.setFont(self.FONT_BOLD, 10)
        self.canvas.setFillColor(self.COLOR_TEXT_DARK)
        self.canvas.drawString(x, y, title)
        
        # Underline
        self.canvas.setStrokeColor(self.COLOR_TEXT_DARK)
        self.canvas.setLineWidth(1)
        self.canvas.line(x, y - 2 * mm, x + 50 * mm, y - 2 * mm)
    
    def _render_main_content(self):
        """Render main content area on the right"""
        main_x = 85 * mm  # Start after sidebar
        main_width = self.width - main_x - self.MARGIN_RIGHT
        current_y = self.y
        
        for section in self.sections:
            # Check page break
            if current_y < 30 * mm:
                self.canvas.showPage()
                current_y = self.height - 20 * mm
            
            # Section title
            self.canvas.setFont(self.FONT_BOLD, 12)
            self.canvas.setFillColor(self.COLOR_TEXT_DARK)
            self.canvas.drawString(main_x, current_y, section.title)
            
            # Underline
            self.canvas.setStrokeColor(self.COLOR_TEXT_DARK)
            self.canvas.setLineWidth(1.5)
            self.canvas.line(main_x, current_y - 2 * mm, 
                           main_x + 40 * mm, current_y - 2 * mm)
            
            current_y -= 8 * mm
            
            # Render content based on style
            if section.style == 'experience':
                current_y = self._render_experience_content(main_x, main_width, 
                                                           section.content, current_y)
            elif section.style == 'two_column':
                current_y = self._render_skills_content(main_x, section.content, current_y)
            else:
                current_y = self._render_paragraph_content(main_x, main_width, 
                                                          section.content, current_y)
            
            current_y -= 6 * mm  # Space between sections
    
    def _render_experience_content(self, x, width, lines, start_y):
        """Render experience entries with bold titles and date alignment"""
        current_y = start_y
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # Check if this is a title line (job title or project name)
            if not line.startswith(('•', '-', '*')) and len(line) < 80:
                # Title in bold
                self.canvas.setFont(self.FONT_BOLD, 10)
                self.canvas.setFillColor(self.COLOR_TEXT_DARK)
                
                # Check for date at end
                date_match = self._extract_date(line)
                if date_match:
                    title_text = line[:date_match['start']].strip()
                    date_text = date_match['date']
                    
                    # Draw title
                    self.canvas.drawString(x, current_y, title_text)
                    
                    # Draw date right-aligned
                    self.canvas.setFont(self.FONT_NAME, 9)
                    date_width = self.canvas.stringWidth(date_text, self.FONT_NAME, 9)
                    self.canvas.drawString(x + width - date_width, current_y, date_text)
                else:
                    self.canvas.drawString(x, current_y, line)
                
                current_y -= 5 * mm
            
            # Company/institution line
            elif i + 1 < len(lines) and not lines[i + 1].startswith(('•', '-', '*')):
                self.canvas.setFont(self.FONT_NAME, 9)
                self.canvas.setFillColor(self.COLOR_ACCENT)
                self.canvas.drawString(x, current_y, line)
                current_y -= 4 * mm
            
            # Bullet points
            elif line.startswith(('•', '-', '*')):
                self.canvas.setFont(self.FONT_NAME, 9)
                self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
                
                # Draw bullet
                self.canvas.drawString(x + 2 * mm, current_y, '•')
                
                # Wrap and draw text
                text = line[1:].strip()
                wrapped = self._wrap_text(text, 75)
                for j, wline in enumerate(wrapped):
                    indent = 6 * mm if j == 0 else 6 * mm
                    self.canvas.drawString(x + indent, current_y, wline)
                    current_y -= 4 * mm
            
            # Regular text
            else:
                self.canvas.setFont(self.FONT_NAME, 9)
                self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
                wrapped = self._wrap_text(line, 85)
                for wline in wrapped:
                    self.canvas.drawString(x, current_y, wline)
                    current_y -= 4 * mm
            
            i += 1
            
            # Page break check
            if current_y < 20 * mm:
                self.canvas.showPage()
                current_y = self.height - 20 * mm
        
        return current_y
    
    def _render_skills_content(self, x, lines, start_y):
        """Render skills in categorized format"""
        current_y = start_y
        
        # Join all lines and parse categories
        full_text = ' '.join(lines)
        categories = self._parse_skills_categories(full_text)
        
        for category, skills in categories.items():
            if not skills:
                continue
            
            # Category name in bold
            self.canvas.setFont(self.FONT_BOLD, 9)
            self.canvas.setFillColor(self.COLOR_TEXT_DARK)
            self.canvas.drawString(x, current_y, category)
            current_y -= 5 * mm
            
            # Skills in regular font
            self.canvas.setFont(self.FONT_NAME, 9)
            self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
            
            # Format skills as comma-separated or bullet list
            skills_text = ', '.join(skills[:6])  # Limit to 6 per category
            wrapped = self._wrap_text(skills_text, 85)
            for line in wrapped:
                self.canvas.drawString(x + 3 * mm, current_y, line)
                current_y -= 4 * mm
            
            current_y -= 2 * mm
        
        return current_y
    
    def _render_paragraph_content(self, x, width, lines, start_y):
        """Render paragraph-style content (Profile, Interests)"""
        current_y = start_y
        self.canvas.setFont(self.FONT_NAME, 9)
        self.canvas.setFillColor(self.COLOR_TEXT_NORMAL)
        
        for line in lines:
            if line.startswith(('•', '-', '*')):
                # Bullet point
                self.canvas.drawString(x + 2 * mm, current_y, '•')
                text = line[1:].strip()
                wrapped = self._wrap_text(text, 80)
                for j, wline in enumerate(wrapped):
                    indent = 6 * mm if j == 0 else 6 * mm
                    self.canvas.drawString(x + indent, current_y, wline)
                    current_y -= 4 * mm
            else:
                wrapped = self._wrap_text(line, 90)
                for wline in wrapped:
                    self.canvas.drawString(x, current_y, wline)
                    current_y -= 4 * mm
            
            # Page break
            if current_y < 20 * mm:
                self.canvas.showPage()
                current_y = self.height - 20 * mm
        
        return current_y
    
    def _extract_date(self, text: str) -> Optional[Dict]:
        """Extract date range from text"""
        import re
        
        # Match patterns like "Jan 2023 - Present" or "2021 - 2024"
        patterns = [
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\s*[-–]\s*(Present|\d{4}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*\d{4}?',
            r'\d{4}\s*[-–]\s*(Present|\d{4})',
            r'(Oct|Jun)\.\s*\d{4}\s*[-–]\s*(Present|Oct|Jun)\.\s*\d{4}?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return {
                    'start': match.start(),
                    'end': match.end(),
                    'date': match.group(0)
                }
        return None
    
    def _parse_skills_categories(self, text: str) -> Dict[str, List[str]]:
        """Parse skills text into categories"""
        categories = {
            'Languages': [],
            'Frameworks': [],
            'Databases': [],
            'Tools': [],
            'APIs & Data Methods': [],
            'Other': []
        }
        
        # Simple keyword matching
        text_lower = text.lower()
        
        # Extract skills based on common keywords
        all_skills = [s.strip() for s in text.replace(',', ' ').replace('•', '').split()]
        
        for skill in all_skills:
            skill_lower = skill.lower()
            if any(x in skill_lower for x in ['python', 'javascript', 'java', 'c++', 'html', 'css']):
                categories['Languages'].append(skill)
            elif any(x in skill_lower for x in ['django', 'flask', 'react', 'angular', 'bootstrap']):
                categories['Frameworks'].append(skill)
            elif any(x in skill_lower for x in ['sql', 'postgresql', 'mongodb', 'mysql']):
                categories['Databases'].append(skill)
            elif any(x in skill_lower for x in ['git', 'docker', 'aws', 'vscode']):
                categories['Tools'].append(skill)
            elif any(x in skill_lower for x in ['api', 'rest', 'json', 'xml']):
                categories['APIs & Data Methods'].append(skill)
            else:
                categories['Other'].append(skill)
        
        # Remove empty categories
        return {k: v for k, v in categories.items() if v}
    
    def _wrap_text(self, text: str, max_chars: int) -> List[str]:
        """Simple word wrapping"""
        words = text.split()
        lines = []
        current = ""
        
        for word in words:
            if len(current) + len(word) + 1 <= max_chars:
                current += " " + word if current else word
            else:
                lines.append(current)
                current = word
        
        if current:
            lines.append(current)
        
        return lines if lines else [text]


class CoverLetterRenderer:
    """
    Renders professional, humble, and polite cover letter PDF
    Matching the CV template style
    """
    
    def __init__(self, personal_info: Dict, company_name: str, job_title: str, 
                 cover_letter_text: str):
        self.personal_info = personal_info
        self.company_name = company_name
        self.job_title = job_title
        self.cover_letter_text = cover_letter_text
        self.buffer = BytesIO()
        self.canvas = canvas.Canvas(self.buffer, pagesize=A4)
        
        # Colors matching CV
        self.COLOR_HEADER = HexColor('#2c3e50')
        self.COLOR_TEXT = HexColor('#333333')
        
    def render(self) -> BytesIO:
        """Generate professional cover letter PDF"""
        from datetime import datetime
        
        left = 25 * mm
        right = self.canvas._pagesize[0] - 25 * mm
        y = A4[1] - 20 * mm
        
        # Header bar (matching CV style)
        self.canvas.setFillColor(self.COLOR_HEADER)
        self.canvas.rect(0, A4[1] - 15 * mm, A4[0], 15 * mm, fill=1, stroke=0)
        
        # Name in header
        self.canvas.setFillColor(HexColor('#ffffff'))
        self.canvas.setFont("Helvetica-Bold", 14)
        name = self.personal_info.get('name', 'Candidate Name')
        self.canvas.drawString(left, A4[1] - 10 * mm, name)
        
        y = A4[1] - 35 * mm
        
        # Contact info line
        self.canvas.setFillColor(self.COLOR_TEXT)
        self.canvas.setFont("Helvetica", 9)
        contact_parts = []
        if self.personal_info.get('email'):
            contact_parts.append(self.personal_info['email'])
        if self.personal_info.get('phone'):
            contact_parts.append(self.personal_info['phone'])
        if self.personal_info.get('location'):
            contact_parts.append(self.personal_info['location'])
        
        if contact_parts:
            contact_line = " | ".join(contact_parts)
            self.canvas.drawString(left, y, contact_line)
            y -= 8 * mm
        
        # Date
        self.canvas.setFont("Helvetica", 10)
        self.canvas.drawString(left, y, datetime.now().strftime('%B %d, %Y'))
        y -= 12 * mm
        
        # Horizontal line
        self.canvas.setStrokeColor(self.COLOR_HEADER)
        self.canvas.setLineWidth(1)
        self.canvas.line(left, y, right, y)
        y -= 10 * mm
        
        # Recipient
        self.canvas.setFont("Helvetica-Bold", 10)
        self.canvas.drawString(left, y, "Dear Hiring Manager,")
        y -= 8 * mm
        
        # Company reference
        if self.company_name:
            self.canvas.setFont("Helvetica", 10)
            self.canvas.drawString(left, y, f"{self.company_name}")
            y -= 12 * mm
        
        # Body paragraphs
        body = self._extract_body()
        self.canvas.setFont("Helvetica", 10)
        self.canvas.setFillColor(self.COLOR_TEXT)
        
        for para in body:
            if para and len(para) > 10:
                # Indent first line slightly for professionalism
                wrapped = self._wrap_text(para, 85)
                for i, line in enumerate(wrapped):
                    x_offset = left + (3 * mm if i == 0 else 0)
                    self.canvas.drawString(x_offset, y, line)
                    y -= 5 * mm
                y -= 3 * mm  # Paragraph spacing
                
                # Page break if needed
                if y < 40 * mm:
                    self.canvas.showPage()
                    y = A4[1] - 20 * mm
                    self.canvas.setFont("Helvetica", 10)
        
        # Closing section
        y -= 8 * mm
        
        # Polite closing
        self.canvas.setFont("Helvetica", 10)
        closing_text = "Thank you for considering my application. I would welcome the opportunity to discuss how my skills and enthusiasm can contribute to your team."
        wrapped = self._wrap_text(closing_text, 85)
        for line in wrapped:
            self.canvas.drawString(left, y, line)
            y -= 5 * mm
        
        y -= 8 * mm
        
        # Sign-off
        self.canvas.drawString(left, y, "Sincerely,")
        y -= 10 * mm
        
        # Name signature style
        self.canvas.setFont("Helvetica-Bold", 11)
        self.canvas.drawString(left, y, self.personal_info.get('name', 'Candidate Name'))
        
        self.canvas.save()
        self.buffer.seek(0)
        return self.buffer
    
    def _extract_body(self) -> List[str]:
        """Extract body text from cover letter, making it humble and professional"""
        lines = self.cover_letter_text.split('\n')
        
        body_lines = []
        in_body = False
        
        for line in lines:
            line = line.strip()
            
            # Skip header/salutation lines
            if any(skip in line for skip in ['Dear', 'Hiring Manager', 'To Whom', 'Subject:']):
                in_body = True
                continue
            
            # Stop at closing
            if any(closing in line for closing in ['Sincerely', 'Best regards', 'Yours faithfully', 'Thank you']):
                break
            
            if in_body and line:
                # Make tone more humble if needed
                humble_line = self._make_humble(line)
                body_lines.append(humble_line)
        
        return body_lines
    
    def _make_humble(self, text: str) -> str:
        """Adjust tone to be more humble and polite"""
        # Replace arrogant phrases with humble ones
        replacements = {
            'I am the best': 'I am eager to bring',
            'I am perfect': 'I am enthusiastic about',
            'I guarantee': 'I am committed to',
            'I will definitely': 'I look forward to',
            'expert in': 'passionate about',
            'master of': 'dedicated to learning',
        }
        
        for arrogant, humble in replacements.items():
            text = text.replace(arrogant, humble)
        
        return text
    
    def _wrap_text(self, text: str, max_chars: int) -> List[str]:
        """Word wrap text"""
        words = text.split()
        lines = []
        current = ""
        
        for word in words:
            if len(current) + len(word) + 1 <= max_chars:
                current += " " + word if current else word
            else:
                lines.append(current)
                current = word
        
        if current:
            lines.append(current)
        
        return lines if lines else [text]


# Convenience functions for backward compatibility

def render_cv_pdf(personal_info: Dict, sections: Dict[str, str]) -> BytesIO:
    """Convenience function to render CV PDF"""
    renderer = CVTemplateRenderer(personal_info, sections)
    return renderer.render()


def render_cover_letter_pdf(personal_info: Dict, company_name: str, 
                           job_title: str, cover_letter_text: str) -> BytesIO:
    """Convenience function to render Cover Letter PDF"""
    renderer = CoverLetterRenderer(personal_info, company_name, job_title, 
                                   cover_letter_text)
    return renderer.render()