import re
from pathlib import Path

from bs4 import BeautifulSoup

FIELD_MAP = {
    "full_name": ["full name", "fullname", "your name", "legal name", "applicant name"],
    "first_name": ["first name", "given name", "firstname", "fname"],
    "last_name": ["last name", "surname", "family name", "lastname", "lname"],
    "email": ["email", "e-mail", "email address"],
    "phone": ["phone", "mobile", "telephone", "phone number", "cell"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile"],
    "github": ["github", "portfolio github", "git hub"],
    "website": ["website", "portfolio", "personal site", "homepage"],
    "location": ["location", "city", "current location", "where are you based"],
}

FILE_HINTS = ("resume", "cv", "curriculum", "attach", "upload")
COVER_HINTS = ("cover letter", "coverletter", "motivation")
APPLY_BUTTON = re.compile(
    r"^(apply|apply now|submit application|submit|send application|continue|next|start application)$",
    re.I,
)


def split_name(full_name: str):
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def field_value(profile, key):
    first, last = split_name(profile.get("full_name") or "")
    values = {
        "full_name": profile.get("full_name") or "",
        "first_name": first,
        "last_name": last,
        "email": profile.get("email") or "",
        "phone": profile.get("phone") or "",
        "linkedin": profile.get("linkedin") or "",
        "github": profile.get("github") or "",
        "website": profile.get("website") or "",
        "location": profile.get("location") or "",
    }
    return values.get(key, "")


def label_for(el, soup):
    bits = []
    for attr in ("aria-label", "placeholder", "name", "id", "autocomplete"):
        v = el.get(attr)
        if v:
            bits.append(str(v))
    eid = el.get("id")
    if eid:
        lab = soup.find("label", attrs={"for": eid})
        if lab:
            bits.append(lab.get_text(" ", strip=True))
    parent_label = el.find_parent("label")
    if parent_label:
        bits.append(parent_label.get_text(" ", strip=True))
    return " ".join(bits).lower()


def match_key(label: str):
    for key, aliases in FIELD_MAP.items():
        for a in aliases:
            if a in label:
                return key
    return None


def click_apply_entry(page):
    candidates = page.locator(
        "a, button, input[type=submit], [role=button]"
    )
    n = min(candidates.count(), 80)
    for i in range(n):
        el = candidates.nth(i)
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or el.get_attribute("value") or "").strip()
            if APPLY_BUTTON.match(text.strip()):
                el.click(timeout=4000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def fill_inputs(page, profile):
    filled = 0
    html = page.content()
    soup = BeautifulSoup(html, "lxml")
    inputs = soup.select("input, textarea, select")
    first, last = split_name(profile.get("full_name") or "")

    for inp in inputs:
        itype = (inp.get("type") or "text").lower()
        if itype in ("hidden", "submit", "button", "checkbox", "radio", "image"):
            continue
        name = inp.get("name") or inp.get("id")
        if not name:
            continue
        label = label_for(inp, soup)
        key = match_key(label)
        value = field_value(profile, key) if key else ""
        if not value:
            if itype == "email":
                value = profile.get("email") or ""
            elif itype == "tel":
                value = profile.get("phone") or ""
            elif "name" in label and "first" not in label and "last" not in label:
                value = profile.get("full_name") or ""
        if not value:
            continue
        selector = None
        if inp.get("id"):
            selector = f"#{inp.get('id')}"
        elif inp.get("name"):
            selector = f"[name=\"{inp.get('name')}\"]"
        if not selector:
            continue
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            tag = inp.name
            if tag == "select":
                continue
            loc.fill(value, timeout=2500)
            filled += 1
        except Exception:
            try:
                page.evaluate(
                    """([sel, val]) => {
                        const el = document.querySelector(sel);
                        if (!el) return;
                        el.focus();
                        el.value = val;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    [selector, value],
                )
                filled += 1
            except Exception:
                pass
    _ = (first, last)
    return filled


def upload_resume(page, resume_path: str):
    if not resume_path or not Path(resume_path).exists():
        return False
    files = page.locator("input[type=file]")
    n = min(files.count(), 8)
    uploaded = False
    for i in range(n):
        el = files.nth(i)
        try:
            name = " ".join(
                filter(
                    None,
                    [
                        el.get_attribute("name") or "",
                        el.get_attribute("id") or "",
                        el.get_attribute("accept") or "",
                    ],
                )
            ).lower()
            if any(h in name for h in COVER_HINTS) and "resume" not in name:
                continue
            el.set_input_files(resume_path, timeout=4000)
            uploaded = True
        except Exception:
            continue
    if uploaded:
        return True
    if n > 0:
        try:
            files.first.set_input_files(resume_path, timeout=4000)
            return True
        except Exception:
            return False
    return False


def maybe_submit(page, auto_submit: bool):
    if not auto_submit:
        return False
    buttons = page.locator("button, input[type=submit], [role=button]")
    n = min(buttons.count(), 60)
    for i in range(n):
        el = buttons.nth(i)
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or el.get_attribute("value") or "").strip().lower()
            if text in ("submit application", "submit", "send application", "apply", "apply now"):
                el.click(timeout=4000)
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    return False


def page_title(page):
    try:
        t = (page.title() or "").strip()
        return t[:180]
    except Exception:
        return ""


def apply_on_page(page, job_url, profile, resume_path, auto_submit, screenshot_dir: Path, job_id: int):
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1800)
    title = page_title(page)
    clicked = click_apply_entry(page)
    page.wait_for_timeout(1200)
    filled = fill_inputs(page, profile)
    uploaded = upload_resume(page, resume_path)
    submitted = maybe_submit(page, auto_submit)
    if not title:
        title = page_title(page)
    shot = screenshot_dir / f"job-{job_id}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
        shot_path = str(shot)
    except Exception:
        shot_path = ""
    return {
        "clicked_apply": clicked,
        "fields_filled": filled,
        "resume_uploaded": uploaded,
        "submitted": submitted,
        "final_url": page.url,
        "page_title": title,
        "screenshot_path": shot_path,
    }
