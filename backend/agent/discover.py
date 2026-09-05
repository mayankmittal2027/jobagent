import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from backend.agent.portals import is_job_posting_url

JOB_PATH = re.compile(
    r"(jobs?|careers?|opening|position|vacanc|role|apply|lever\.co|greenhouse|ashbyhq|myworkdayjobs|smartrecruiters)",
    re.I,
)
SKIP = re.compile(
    r"(login|signin|privacy|terms|cookie|facebook|twitter|linkedin\.com/share|"
    r"instagram|youtube|mailto:|javascript:|#|/legal|/investors|/customers|"
    r"/partners|/demo|/help|/support|/products?/|/blog|/news|/press)",
    re.I,
)
TITLE_SKIP = re.compile(
    r"^(home|careers?|jobs?|openings?|about|contact|privacy|apply now|"
    r"view all|see all|learn more|legal|leadership|investors?|customers?|"
    r"partners?|newsroom|locations?|overview|support|login|demo|search)$",
    re.I,
)


def _abs(base, href):
    if not href:
        return None
    href = href.strip()
    if href.startswith("javascript:") or href.startswith("mailto:"):
        return None
    return urljoin(base, href)


def extract_jobs_from_html(html: str, page_url: str, company_name: str):
    soup = BeautifulSoup(html, "lxml")
    found = {}
    for a in soup.find_all("a", href=True):
        href = _abs(page_url, a.get("href"))
        if not href or SKIP.search(href):
            continue
        if not is_job_posting_url(href) and not JOB_PATH.search(href):
            continue
        if not is_job_posting_url(href):
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text or len(text) < 4 or len(text) > 180:
            continue
        if TITLE_SKIP.match(text):
            continue
        key = href.split("?")[0].rstrip("/")
        if key in found:
            continue
        loc_el = a.find_next(
            string=re.compile(
                r"(remote|hybrid|onsite|full.?time|part.?time|, [A-Z]{2}\b|noida|bengaluru|bangalore|india)",
                re.I,
            )
        )
        location = " ".join(str(loc_el).split())[:80] if loc_el else ""
        found[key] = {
            "title": text[:160],
            "url": href,
            "location": location,
            "company_name": company_name,
            "description": "",
        }
    return list(found.values())


def harvest_listing_page(page, career_url: str, company_name: str):
    page.goto(career_url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
    except Exception:
        pass
    html = page.content()
    jobs = extract_jobs_from_html(html, page.url, company_name)
    extra = []
    for job in jobs[:15]:
        try:
            page.goto(job["url"], wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(1000)
            soup = BeautifulSoup(page.content(), "lxml")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ", strip=True).split())[:4000]
            job["description"] = text
            extra.append(job)
        except Exception:
            extra.append(job)
    return extra or jobs
