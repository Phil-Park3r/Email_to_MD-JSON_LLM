"""
HTML processing functions for the email pipeline.
"""

from bs4 import BeautifulSoup, NavigableString
import re

class HTMLConverter:
    """
    Convert HTML to text while preserving important formatting and structure.
    Mimics html2text functionality using BeautifulSoup4.
    """
    def __init__(self):
        self.ignore_links = False
        self.ignore_images = True
        self.ignore_tables = False
        self.body_width = 0
        
    def _handle_tables(self, soup):
        """Convert tables to markdown-style format if not ignored."""
        if self.ignore_tables:
            for table in soup.find_all('table'):
                table.decompose()
        else:
            for table in soup.find_all('table'):
                rows = []
                for tr in table.find_all('tr'):
                    cols = []
                    for td in tr.find_all(['td', 'th']):
                        cols.append(td.get_text().strip())
                    rows.append(' | '.join(cols))
                if rows:
                    markdown_table = '\n'.join(rows)
                    new_tag = soup.new_tag('div')
                    new_tag.string = f"\n\n{markdown_table}\n\n"
                    table.replace_with(new_tag)

    def _handle_links(self, soup):
        """Convert links to markdown format if not ignored."""
        if not self.ignore_links:
            for a in soup.find_all('a'):
                href = a.get('href', '')
                text = a.get_text().strip()
                if href and text:
                    if href != text:
                        markdown_link = f"[{text}]({href})"
                    else:
                        markdown_link = href
                    new_tag = soup.new_tag('span')
                    new_tag.string = markdown_link
                    a.replace_with(new_tag)

    def _handle_images(self, soup):
        """Remove or convert images based on settings."""
        if self.ignore_images:
            for img in soup.find_all('img'):
                img.decompose()
        else:
            for img in soup.find_all('img'):
                alt = img.get('alt', '')
                src = img.get('src', '')
                if src:
                    markdown_img = f"![{alt}]({src})"
                    new_tag = soup.new_tag('span')
                    new_tag.string = markdown_img
                    img.replace_with(new_tag)

    def _handle_lists(self, soup):
        """Convert lists to markdown format."""
        for ul in soup.find_all('ul'):
            items = []
            for li in ul.find_all('li', recursive=False):
                items.append(f"* {li.get_text().strip()}")
            if items:
                new_tag = soup.new_tag('div')
                new_tag.string = '\n'.join(items) + '\n'
                ul.replace_with(new_tag)

        for ol in soup.find_all('ol'):
            items = []
            for i, li in enumerate(ol.find_all('li', recursive=False), 1):
                items.append(f"{i}. {li.get_text().strip()}")
            if items:
                new_tag = soup.new_tag('div')
                new_tag.string = '\n'.join(items) + '\n'
                ol.replace_with(new_tag)

    def _preserve_line_breaks(self, soup):
        """Preserve meaningful line breaks and spacing."""
        for br in soup.find_all('br'):
            br.replace_with('\n')
        
        for p in soup.find_all(['p', 'div']):
            if p.find_all(['p', 'div', 'br']) or p.get_text().strip():
                p.append('\n\n')

    def handle(self, html_content: str) -> str:
        """
        Convert HTML to markdown-style text while preserving structure.
        """
        soup = BeautifulSoup(html_content, 'lxml')

        # Remove script and style elements
        for element in soup(['script', 'style']):
            element.decompose()

        self._handle_tables(soup)
        self._handle_links(soup)
        self._handle_images(soup)
        self._handle_lists(soup)
        self._preserve_line_breaks(soup)

        # Get text while preserving line breaks
        text = soup.get_text()
        
        # Clean up whitespace while preserving structure
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

def create_html2text_converter() -> HTMLConverter:
    """
    Create a configured HTML converter that mimics html2text behavior.
    """
    converter = HTMLConverter()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.ignore_tables = False
    converter.body_width = 0
    return converter

def convert_html_to_text(html_content: str) -> str:
    """
    Convert HTML to text using BeautifulSoup, removing styling/formatting,
    then remove extraneous whitespace but keep paragraph separation.
    Maintains compatibility with previous html2text implementation.
    """
    converter = create_html2text_converter()
    plain = converter.handle(html_content)
    plain = re.sub(r'\n{3,}', '\n\n', plain).strip()
    return plain 