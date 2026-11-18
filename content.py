from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString

ROOT = Path(__file__).resolve().parent
PAGES = {
    "index": "Басты бет", "about": "Департамент туралы", "team": "Команда",
    "projects": "Ғылыми жобалар", "grants": "Гранттар", "mou": "MOU",
    "news": "Жаңалықтар", "contact": "Байланыс",
}

COMMON = [
    ("title", "Браузердегі бет атауы", "title"),
    ("description", "Іздеу жүйесіне арналған сипаттама", 'meta[name="description"]'),
]
HOME = [
    ("tagline", "Негізгі тақырып үстіндегі мәтін", ".hero .eyebrow"),
    ("heading", "Негізгі тақырып", ".hero h1"),
    ("intro", "Кіріспе", ".hero p"),
    ("projects_button", "Жобалар батырмасы", ".hero .primary"),
    ("about_button", "Департамент батырмасы", ".hero .secondary"),
    ("directory_label", "Бағыттар тақырыбы үстіндегі мәтін", ".section-heading .eyebrow"),
    ("directory_title", "Бағыттар тақырыбы", ".section-heading h2"),
    ("directory_intro", "Бағыттар сипаттамасы", ".section-heading p"),
    ("contact_label", "Байланыс тақырыбы үстіндегі мәтін", ".contact-area .eyebrow"),
    ("contact_title", "Байланыс тақырыбы", ".contact-area h2"),
    ("contact_intro", "Байланыс сипаттамасы", ".contact-area p"),
    ("contact_button", "Байланыс батырмасы", ".contact-area .secondary"),
]
INNER = [
    ("heading", "Бет тақырыбы", ".subhero h1"),
    ("intro", "Кіріспе", ".subhero p"),
    ("notice", "Мысалдар туралы ескертпе", ".example-notice"),
]


def read_text(tag):
    if tag.name == "meta":
        return tag["content"]
    if tag.select_one("br"):
        return "".join("\n" if child.name == "br" else str(child)
                       if isinstance(child, NavigableString) else ""
                       for child in tag.children).strip()
    return "".join(tag.find_all(string=True, recursive=False)).strip()


def write_text(tag, value):
    if tag.name == "meta":
        tag["content"] = value
        return
    if tag.select_one("br"):
        tag.br.decompose()
    existing = tag.find_all(string=True, recursive=False)
    if existing:
        existing[0].replace_with(NavigableString(value + (" " if tag.name == "a" else "")))
        for child in existing[1:]:
            child.extract()
    else:
        tag.insert(0, NavigableString(value))


def page_template(page):
    soup = BeautifulSoup((ROOT / f"{page}.html").read_text(encoding="utf-8"), "html.parser")
    fields = {key: (label, soup.select_one(selector))
              for key, label, selector in COMMON + (HOME if page == "index" else INNER)}
    sections = {}
    if page == "index":
        for card in soup.select(".directory .card"):
            slug = card["id"]
            fields[f"{slug}_title"] = (f"{PAGES[slug]} · карточка тақырыбы", card.select_one("h3 a"))
            fields[f"{slug}_description"] = (f"{PAGES[slug]} · карточка сипаттамасы", card.p)
            for link in card.select(".directory-links a"):
                fields[f"{slug}_{link['href'].split('#')[1]}"] = (f"{PAGES[slug]} · {link.get_text()}", link)
    else:
        for section in soup.select(".example-card"):
            anchor = section["id"]
            heading = section.h2
            fields[f"section_{anchor}"] = (f"Бөлім атауы · {heading.get_text()}", heading)
            sections[anchor] = section
    return soup, fields, sections


def default_records(section):
    article = section.select_one(".example-content")
    related = article.select_one(".related-link")
    return [{
        "title": article.h3.get_text(),
        "body": article.p.get_text(),
        "example": bool(article.select_one(".example-label")),
        "label": read_text(related) if related else "",
        "url": related["href"] if related else "",
    }]


def valid_link(value):
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value or value.startswith("//"):
        return False
    if url.scheme in {"http", "https"}:
        return bool(url.hostname) and not (url.username or url.password)
    return not url.scheme and not url.netloc and (url.path in {f"{page}.html" for page in PAGES}
                                                   or bool(url.fragment and not url.path))


def render_page(page, saved):
    soup, fields, sections = page_template(page)
    for key, value in saved.get("fields", {}).items():
        if key in fields:
            write_text(fields[key][1], value)

    for anchor, section in sections.items():
        jump = soup.select_one(f'.section-links a[href="#{anchor}"]')
        jump.string = section.h2.get_text()
        if anchor not in saved.get("records", {}):
            continue
        section.select_one(".example-content").decompose()
        for record in saved["records"][anchor]:
            article = soup.new_tag("article", attrs={"class": "example-content"})
            for name, value, css in (
                ("span", "МЫСАЛ" if record["example"] else "", "example-label"),
                ("h3", record["title"], ""),
                ("p", record["body"], ""),
                ("a", record["label"], "related-link"),
            ):
                if not value:
                    continue
                child = soup.new_tag(name, attrs={"class": css} if css else {})
                if name == "a":
                    child["href"] = record["url"]
                child.string = value
                article.append(child)
            section.append(article)

    if sections and not soup.select_one(".example-label"):
        soup.select_one(".example-notice").decompose()
    return str(soup)
