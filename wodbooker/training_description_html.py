"""Sanitize and prepare WodBuster training description HTML for display."""
import re
from html import unescape

from bs4 import BeautifulSoup

_ALLOWED_TAGS = frozenset({
    'h1', 'h2', 'div', 'span', 'p', 'br', 'strong', 'u',
})
_STYLE_ALLOWLIST = frozenset({
    'text-decoration', 'text-align', 'max-width', 'aspect-ratio', 'position',
})
_CLASS_ALLOWLIST = frozenset({
    'text-center', 'movimientos', 'flex-video', 'widescreen', 'bmc',
})
_HTML_TAG_RE = re.compile(r'<[a-zA-Z][\w-]*', re.IGNORECASE)
_MOVIMIENTOS_HEADER_RE = re.compile(r'^movimientos?$|^movements?$', re.IGNORECASE)


def description_is_html(text: str) -> bool:
    """True if text looks like HTML markup (not legacy plain-text cache)."""
    if not text or not str(text).strip():
        return False
    s = str(text).strip()
    return '<' in s and bool(_HTML_TAG_RE.search(s))


def extract_board_title(description_html: str, fallback: str = '') -> str:
    """Board title from WodBuster <h1> (e.g. Hybrid), else fallback schedule name."""
    if not description_html or not description_html.strip():
        return fallback
    h1 = BeautifulSoup(description_html, 'html.parser').find('h1')
    if h1:
        title = h1.get_text(strip=True)
        if title:
            return title
    return fallback


def visible_text_len(html_or_text: str) -> int:
    """Length of visible text after stripping HTML (for empty-content checks)."""
    if not html_or_text or not html_or_text.strip():
        return 0
    if '<' in html_or_text:
        return len(BeautifulSoup(html_or_text, 'html.parser').get_text(strip=True))
    return len(html_or_text.strip())


def _filter_style(style_value: str) -> str:
    if not style_value:
        return ''
    parts = []
    for decl in style_value.split(';'):
        decl = decl.strip()
        if not decl or ':' not in decl:
            continue
        prop, val = decl.split(':', 1)
        prop = prop.strip().lower()
        if prop in _STYLE_ALLOWLIST:
            parts.append(f'{prop}: {val.strip()}')
    return '; '.join(parts)


def _decompose_tag_and_following_siblings(tag) -> None:
    while tag is not None:
        sibling = tag.find_next_sibling()
        tag.decompose()
        tag = sibling


def _tag_classes(tag) -> list:
    """Return class list from a tag; BS4 may leave attrs as None on some nodes."""
    attrs = getattr(tag, 'attrs', None)
    if not attrs:
        return []
    raw = attrs.get('class')
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return [str(raw)]


def _strip_movimientos_section(html: str) -> str:
    """Remove Movimientos block and everything after it (movement videos)."""
    soup = BeautifulSoup(html, 'html.parser')
    for div in list(soup.find_all('div')):
        if any('movimientos' in cls.lower() for cls in _tag_classes(div)):
            div.decompose()
    for h2 in list(soup.find_all('h2')):
        if _MOVIMIENTOS_HEADER_RE.match(h2.get_text(strip=True)):
            _decompose_tag_and_following_siblings(h2)
    return ''.join(str(c) for c in soup.children).strip()


def _sanitize_element(tag) -> None:
    if tag.name == 'iframe':
        tag.decompose()
        return

    if tag.name == 'canvas':
        tag.decompose()
        return

    if tag.name not in _ALLOWED_TAGS:
        tag.unwrap()
        return

    attrs = dict(tag.attrs or {})
    for attr in list(attrs):
        if attr == 'class':
            classes = [c for c in _tag_classes(tag) if c in _CLASS_ALLOWLIST]
            if classes:
                tag['class'] = classes
            else:
                del tag[attr]
        elif attr == 'style':
            filtered = _filter_style(tag.get('style', ''))
            if filtered:
                tag['style'] = filtered
            else:
                del tag[attr]
        elif attr not in ('href',):
            del tag[attr]


def sanitize_training_html(html: str) -> str:
    """Return safe HTML subset matching WodBuster workout boards."""
    if not html or not html.strip():
        return ''

    html = _strip_movimientos_section(html)
    soup = BeautifulSoup(html, 'html.parser')
    for tag in reversed(soup.find_all(True)):
        _sanitize_element(tag)

    result = ''.join(str(c) for c in soup.children).strip()
    return unescape(result) if result else ''


def _format_legacy_plain_text(text: str) -> str:
    """Bold likely section headers in old plain-text cached descriptions."""
    if not text:
        return ''

    text = unescape(text.replace('&nbsp;', ' '))
    header_patterns = [
        r'^WARM\s+UP', r'^WARMUP', r'^STRENGTH', r'^STRENGHT', r'^WOD\s*$',
        r'^METCON', r'^COOL\s+DOWN', r'^MOBILITY', r'^STRETCH',
        r'^MOVIMIENTOS', r'^MOVEMENTS', r'^BLOQUE\s+[IVX]+',
    ]
    lines = text.split('\n')
    formatted = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted.append('')
            continue
        if re.match(r'^MOVIMIENTOS', stripped, re.IGNORECASE) or re.match(
            r'^MOVEMENTS', stripped, re.IGNORECASE
        ):
            break
        is_header = any(re.match(p, stripped, re.IGNORECASE) for p in header_patterns)
        if not is_header and len(stripped) < 40 and stripped.isupper() and len(stripped.split()) <= 6:
            is_header = True
        if is_header:
            formatted.append(
                f'<strong style="font-size: 1.1em; font-weight: 700;">{stripped}</strong>'
            )
        else:
            formatted.append(stripped)
    return '<br />\n'.join(formatted)


def prepare_training_description(html_or_text: str) -> str | None:
    """Sanitize WodBuster HTML or format legacy plain text for template |safe."""
    if not html_or_text or not html_or_text.strip():
        return None
    stripped = html_or_text.strip()
    if description_is_html(stripped):
        sanitized = sanitize_training_html(stripped)
        return sanitized or None
    return _format_legacy_plain_text(stripped) or None
