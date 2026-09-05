import re
from urllib.parse import urlparse

COOKIE_LABELS = (
    "accept all",
    "accept all cookies",
    "accept cookies",
    "i agree",
    "agree",
    "got it",
    "allow all",
    "allow cookies",
    "ok",
    "continue",
)

APPLY_NAME = re.compile(
    r"(apply for this job|apply for this position|apply now|start application|"
    r"i.?m interested|i am interested|submit application|apply)",
    re.I,
)

SKIP_CLICK = re.compile(
    r"(privacy|cookie|login|sign in|sign up|create account|share|twitter|facebook|"
    r"linkedin|instagram|watch|learn more|read more|view all|see all jobs|back)",
    re.I,
)

LOGIN_HINT = re.compile(
    r"(sign in to|log in to|create an account|sign in to apply|please (log|sign) in|"
    r"linkedin\.com/login|auth0|okta|onelogin)",
    re.I,
)

CAPTCHA_HINT = re.compile(
    r"(captcha|hcaptcha|recaptcha|verify you are human|cloudflare|attention required|"
    r"checking your browser)",
    re.I,
)

SUCCESS_HINT = re.compile(
    r"(thank you|thanks for (your )?appl|application (has been )?submitted|"
    r"successfully submitted|we (have )?received your|application received|"
    r"your application was sent)",
    re.I,
)


def host_path(url: str):
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower().replace("www.", "")
    path = parsed.path or ""
    return host, path, parsed


def portal_name(url: str) -> str:
    host, path, _ = host_path(url)
    if "lever.co" in host:
        return "lever"
    if "greenhouse.io" in host or "job-boards.greenhouse.io" in host:
        return "greenhouse"
    if "ashbyhq.com" in host:
        return "ashby"
    if "myworkdayjobs.com" in host or "workday.com" in host:
        return "workday"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "icims.com" in host:
        return "icims"
    if "successfactors" in host or "sap.com" in host:
        return "successfactors"
    if "linkedin.com" in host:
        return "linkedin"
    if "naukri.com" in host:
        return "naukri"
    if "indeed.com" in host:
        return "indeed"
    if "/jobs/" in path or "/job/" in path or "/careers/" in path:
        return "generic"
    return "generic"


def company_from_url(url: str) -> str:
    host, path, _ = host_path(url)
    parts = [p for p in path.split("/") if p]
    if "lever.co" in host and parts:
        return parts[0].replace("-", " ").title()
    if "greenhouse.io" in host and parts:
        return parts[0].replace("-", " ").title()
    if "ashbyhq.com" in host and parts:
        return parts[0].replace("-", " ").title()
    if "myworkdayjobs.com" in host:
        name = host.split(".")[0]
        return name.replace("-", " ").title()
    if "smartrecruiters.com" in host and "company" in parts:
        try:
            return parts[parts.index("company") + 1].replace("-", " ").title()
        except Exception:
            pass
    if parts:
        return parts[0].replace("-", " ").title()
    return host.split(".")[0].title() if host else "Unknown"


def is_job_posting_url(url: str) -> bool:
    host, path, parsed = host_path(url)
    if not host or parsed.scheme not in ("http", "https"):
        return False
    blob = f"{host}{path}".lower()
    if any(x in blob for x in (
        "/legal", "/privacy", "/terms", "/cookie", "/login", "/signin",
        "/about", "/blog", "/news", "/press", "/investors", "/customers",
        "/partners", "/demo", "/help", "/support", "/products/", "/product/",
        "/accessibility", "/locations", "/leadership",
    )):
        if not any(x in blob for x in ("/jobs/", "/job/", "/careers/", "lever.co", "greenhouse", "ashbyhq", "myworkdayjobs")):
            return False
    if "lever.co" in host:
        return bool(re.search(r"lever\.co/[^/]+/[0-9a-f-]{8,}", blob))
    if "greenhouse.io" in host:
        return "/jobs/" in path or "/job/" in path
    if "ashbyhq.com" in host:
        return len([p for p in path.split("/") if p]) >= 2
    if "myworkdayjobs.com" in host or "smartrecruiters.com" in host or "icims.com" in host:
        return True
    if "linkedin.com" in host:
        return "/jobs/" in path
    if re.search(r"/jobs?/[^/]+|/careers?/[^/]+|/opening|/position|/vacanc", path, re.I):
        return True
    return False


def apply_url_for(url: str) -> str:
    host, path, parsed = host_path(url)
    clean = url.split("#")[0].rstrip("/")
    if "lever.co" in host and not path.rstrip("/").endswith("/apply"):
        if re.search(r"/[0-9a-f-]{8,}$", path.rstrip("/"), re.I):
            return clean + "/apply"
    if "ashbyhq.com" in host and "/application" not in path:
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return clean + "/application"
    return url


def dismiss_cookies(target):
    for label in COOKIE_LABELS:
        try:
            loc = target.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=1500)
                target.wait_for_timeout(400)
                return True
        except Exception:
            continue
    try:
        loc = target.locator(
            "button, [role=button], a"
        ).filter(has_text=re.compile(r"accept (all|cookies)|agree|got it", re.I))
        if loc.count() and loc.first.is_visible():
            loc.first.click(timeout=1500)
            return True
    except Exception:
        pass
    return False


def _visible_click(locator):
    n = min(locator.count(), 20)
    for i in range(n):
        el = locator.nth(i)
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or el.get_attribute("value") or el.get_attribute("aria-label") or "").strip()
            if not text or SKIP_CLICK.search(text):
                continue
            if re.search(r"submit|send application|finish", text, re.I):
                continue
            if APPLY_NAME.search(text) and len(text) < 80:
                el.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


def click_apply(page):
    try:
        page.wait_for_timeout(400)
        if _visible_click(page.get_by_role("button", name=APPLY_NAME)):
            return True
        if _visible_click(page.get_by_role("link", name=APPLY_NAME)):
            return True
        if _visible_click(page.locator("a, button, input[type=submit], [role=button]")):
            return True
    except Exception:
        pass
    return False


def page_text(page) -> str:
    try:
        return page.inner_text("body")[:8000]
    except Exception:
        try:
            return page.content()[:8000]
        except Exception:
            return ""


def detect_block(page) -> str:
    text = page_text(page)
    url = (page.url or "").lower()
    if "linkedin.com" in url and ("login" in url or "uas/login" in url):
        return "linkedin_login_required"
    if CAPTCHA_HINT.search(text) or CAPTCHA_HINT.search(url):
        return "captcha_or_bot_check"
    if LOGIN_HINT.search(text) and not re.search(r"application|resume|curriculum", text, re.I):
        if any(k in url for k in ("login", "signin", "sign-in", "auth")):
            return "login_required"
    return ""


def detect_success(page) -> bool:
    text = page_text(page)
    url = (page.url or "").lower()
    if SUCCESS_HINT.search(text):
        return True
    if any(k in url for k in ("/thanks", "/thank-you", "application-submitted", "confirmation")):
        return True
    return False


def switch_new_page(context, timeout=4000):
    try:
        new_page = context.wait_for_event("page", timeout=timeout)
        new_page.wait_for_load_state("domcontentloaded")
        return new_page
    except Exception:
        return None
