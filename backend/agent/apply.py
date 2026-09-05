import json
import re
from pathlib import Path

from backend.agent.portals import (
    apply_url_for,
    click_apply,
    company_from_url,
    detect_block,
    detect_success,
    dismiss_cookies,
    portal_name,
)

QUESTION_MAP = [
    (("years of experience", "total experience", "how many years", "years of qa", "years in"), "total_experience"),
    (("notice period", "notice"), "notice_period"),
    (("current ctc", "current compensation", "current salary", "present ctc"), "current_ctc"),
    (("expected ctc", "expected compensation", "expected salary", "desired salary", "salary expectation"), "expected_ctc"),
    (("current location", "current city", "where are you currently", "current address"), "current_location"),
    (("preferred location", "preferred city", "willing to relocate"), "preferred_location"),
    (("work authorization india", "indian citizen", "citizenship"), "work_authorization"),
    (("remote", "work from home", "wfh"), "open_to_remote"),
    (("hybrid",), "open_to_hybrid_ncr"),
    (("linkedin",), "linkedin"),
    (("github",), "github"),
    (("website", "portfolio", "personal site"), "website"),
    (("cover letter", "additional information", "why do you want", "tell us about", "summary"), "cover"),
]

YES_RE = re.compile(r"^(yes|y|true|authorized|i agree|agree)$", re.I)
NO_RE = re.compile(r"^(no|n|false)$", re.I)

KNOWN_SELECTORS = {
    "email": [
        "input[type=email]",
        "input[name=email]",
        "input[name='job_application[email]']",
        "input[autocomplete=email]",
        "input[id*=email i]",
        "input[placeholder*='email' i]",
    ],
    "phone": [
        "input[type=tel]",
        "input[name=phone]",
        "input[name='job_application[phone]']",
        "input[autocomplete=tel]",
        "input[id*=phone i]",
        "input[placeholder*='phone' i]",
        "input[name*=phone i]",
    ],
    "full_name": [
        "input[name=name]",
        "input[name=full_name]",
        "input[name=fullname]",
        "input[autocomplete=name]",
        "input[id=name]",
        "input[placeholder*='full name' i]",
        "input[name='job_application[name]']",
    ],
    "first_name": [
        "input[name=first_name]",
        "input[name=firstName]",
        "input[name='job_application[first_name]']",
        "input[autocomplete=given-name]",
        "input[id*=first_name i]",
        "input[id*=firstName i]",
        "input[placeholder*='first name' i]",
    ],
    "last_name": [
        "input[name=last_name]",
        "input[name=lastName]",
        "input[name='job_application[last_name]']",
        "input[autocomplete=family-name]",
        "input[id*=last_name i]",
        "input[id*=lastName i]",
        "input[placeholder*='last name' i]",
    ],
    "linkedin": [
        "input[name*=linkedin i]",
        "input[id*=linkedin i]",
        "input[placeholder*='linkedin' i]",
        "input[name='urls[LinkedIn]']",
        "input[name='job_application[urls][LinkedIn]']",
    ],
    "github": [
        "input[name*=github i]",
        "input[id*=github i]",
        "input[placeholder*='github' i]",
        "input[name='urls[GitHub]']",
    ],
    "website": [
        "input[name*=website i]",
        "input[name*=portfolio i]",
        "input[placeholder*='website' i]",
        "input[placeholder*='portfolio' i]",
        "input[name='urls[Portfolio]']",
        "input[name='urls[Other]']",
    ],
    "location": [
        "input[name=location]",
        "input[name*=location i]",
        "input[id*=location i]",
        "input[placeholder*='location' i]",
        "input[placeholder*='city' i]",
        "input[autocomplete=address-level2]",
    ],
    "org": [
        "input[name=org]",
        "input[name*=company i]",
        "input[placeholder*='current company' i]",
        "input[placeholder*='company' i]",
    ],
}


def split_name(full_name: str):
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def profile_bundle(profile: dict):
    first, last = split_name(profile.get("full_name") or "")
    answers = profile.get("answers") or {}
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except json.JSONDecodeError:
            answers = {}
    cover = (profile.get("summary") or "").strip()
    return {
        "full_name": profile.get("full_name") or "",
        "first_name": first,
        "last_name": last,
        "email": profile.get("email") or "",
        "phone": profile.get("phone") or "",
        "linkedin": profile.get("linkedin") or answers.get("linkedin") or "",
        "github": profile.get("github") or "",
        "website": profile.get("website") or "",
        "location": profile.get("location") or answers.get("current_location") or "",
        "org": answers.get("current_company") or "",
        "cover": cover,
        "answers": answers,
    }


def _safe_fill(locator, value: str) -> bool:
    if not value:
        return False
    try:
        if locator.count() == 0:
            return False
        el = locator.first
        if not el.is_visible():
            try:
                el.fill(value, timeout=1500, force=True)
                return True
            except Exception:
                return False
        tag = ""
        try:
            tag = (el.evaluate("e => e.tagName") or "").lower()
        except Exception:
            tag = ""
        itype = (el.get_attribute("type") or "text").lower()
        if itype in ("hidden", "submit", "button", "file", "checkbox", "radio", "image"):
            return False
        if tag == "select":
            return _select_best(el, value)
        existing = ""
        try:
            existing = (el.input_value(timeout=800) or "").strip()
        except Exception:
            existing = ""
        if existing and existing.lower() != "select...":
            return False
        el.click(timeout=1500)
        el.fill(value, timeout=2500)
        try:
            el.dispatch_event("input")
            el.dispatch_event("change")
        except Exception:
            pass
        return True
    except Exception:
        try:
            locator.first.evaluate(
                """(el, val) => {
                    el.focus();
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                value,
            )
            return True
        except Exception:
            return False


def _select_best(el, value: str) -> bool:
    try:
        options = el.locator("option")
        n = min(options.count(), 40)
        want = (value or "").strip().lower()
        if not want:
            return False
        best = None
        for i in range(n):
            opt = options.nth(i)
            text = (opt.inner_text() or "").strip()
            val = (opt.get_attribute("value") or "").strip()
            blob = f"{text} {val}".lower()
            if not text or blob in ("select", "select...", "please select", "-"):
                continue
            if want == text.lower() or want == val.lower() or want in blob or blob in want:
                best = val or text
                break
            if YES_RE.match(want) and re.search(r"\byes\b", blob):
                best = val or text
                break
            if NO_RE.match(want) and re.search(r"\bno\b", blob) and "not" not in blob:
                best = val or text
                break
        if best is None:
            return False
        el.select_option(best)
        return True
    except Exception:
        return False


def _fill_selectors(target, selectors, value) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            loc = target.locator(sel)
            if _safe_fill(loc, value):
                return True
        except Exception:
            continue
    return False


def _label_text(el) -> str:
    bits = []
    try:
        for attr in ("aria-label", "placeholder", "name", "id", "autocomplete"):
            v = el.get_attribute(attr)
            if v:
                bits.append(str(v))
        bits.append(el.evaluate(
            """el => {
                const id = el.id;
                if (id) {
                    const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                    if (lab) return lab.innerText || '';
                }
                const parent = el.closest('label');
                if (parent) return parent.innerText || '';
                const wrap = el.closest('li, .application-question, .field, .form-group, div');
                if (wrap) {
                    const l = wrap.querySelector('label, .application-label, h4, h3, p');
                    if (l) return l.innerText || '';
                }
                return '';
            }"""
        ) or "")
    except Exception:
        pass
    return " ".join(bits).lower()


def fill_custom_questions(target, bundle) -> int:
    filled = 0
    answers = bundle.get("answers") or {}
    try:
        fields = target.locator("input:not([type=hidden]):not([type=file]):not([type=submit]):not([type=button]), textarea, select")
        n = min(fields.count(), 80)
    except Exception:
        return 0
    for i in range(n):
        el = fields.nth(i)
        try:
            if not el.is_visible():
                continue
            label = _label_text(el)
            if not label:
                continue
            value = ""
            for hints, key in QUESTION_MAP:
                if any(h in label for h in hints):
                    if key == "cover":
                        value = bundle.get("cover") or ""
                    elif key in bundle:
                        value = bundle.get(key) or answers.get(key) or ""
                    else:
                        value = answers.get(key) or ""
                    break
            if not value:
                continue
            tag = (el.evaluate("e => e.tagName") or "").lower()
            itype = (el.get_attribute("type") or "text").lower()
            if itype in ("checkbox", "radio"):
                if YES_RE.match(value) or "yes" in value.lower() or "authorized" in value.lower():
                    try:
                        if not el.is_checked():
                            el.check(timeout=1500)
                            filled += 1
                    except Exception:
                        pass
                continue
            if tag == "select":
                if _select_best(el, value):
                    filled += 1
                continue
            if _safe_fill(el, value):
                filled += 1
        except Exception:
            continue
    return filled


def fill_core_fields(target, bundle) -> int:
    filled = 0
    for key, selectors in KNOWN_SELECTORS.items():
        if _fill_selectors(target, selectors, bundle.get(key) or ""):
            filled += 1
    try:
        if bundle.get("email"):
            loc = target.get_by_label(re.compile(r"email", re.I))
            if _safe_fill(loc, bundle["email"]):
                filled += 1
        if bundle.get("phone"):
            loc = target.get_by_label(re.compile(r"phone|mobile|telephone", re.I))
            if _safe_fill(loc, bundle["phone"]):
                filled += 1
        if bundle.get("first_name"):
            loc = target.get_by_label(re.compile(r"first name|given name", re.I))
            if _safe_fill(loc, bundle["first_name"]):
                filled += 1
        if bundle.get("last_name"):
            loc = target.get_by_label(re.compile(r"last name|surname|family name", re.I))
            if _safe_fill(loc, bundle["last_name"]):
                filled += 1
        if bundle.get("full_name"):
            loc = target.get_by_label(re.compile(r"^(full name|name)$", re.I))
            if _safe_fill(loc, bundle["full_name"]):
                filled += 1
        if bundle.get("linkedin"):
            loc = target.get_by_label(re.compile(r"linkedin", re.I))
            if _safe_fill(loc, bundle["linkedin"]):
                filled += 1
        if bundle.get("cover"):
            loc = target.get_by_label(re.compile(r"cover letter|additional information|comments", re.I))
            if _safe_fill(loc, bundle["cover"]):
                filled += 1
    except Exception:
        pass
    filled += fill_custom_questions(target, bundle)
    return filled


def upload_resume(target, resume_path: str) -> bool:
    if not resume_path or not Path(resume_path).exists():
        return False
    try:
        files = target.locator("input[type=file]")
        n = min(files.count(), 10)
    except Exception:
        return False
    uploaded = False
    for i in range(n):
        el = files.nth(i)
        try:
            blob = " ".join(
                filter(
                    None,
                    [
                        el.get_attribute("name") or "",
                        el.get_attribute("id") or "",
                        el.get_attribute("accept") or "",
                        el.get_attribute("aria-label") or "",
                    ],
                )
            ).lower()
            if "cover" in blob and "resume" not in blob and "cv" not in blob:
                continue
            el.set_input_files(resume_path, timeout=6000)
            uploaded = True
        except Exception:
            continue
    if uploaded:
        return True
    if n > 0:
        try:
            files.first.set_input_files(resume_path, timeout=6000)
            return True
        except Exception:
            return False
    return False


def tick_required_consents(target) -> int:
    ticked = 0
    try:
        boxes = target.locator("input[type=checkbox]")
        n = min(boxes.count(), 20)
    except Exception:
        return 0
    for i in range(n):
        el = boxes.nth(i)
        try:
            if not el.is_visible():
                continue
            if el.is_checked():
                continue
            label = _label_text(el)
            if any(k in label for k in (
                "privacy", "terms", "consent", "agree", "acknowledge",
                "i have read", "gdpr", "authorized to work", "legally authorized",
            )):
                if any(k in label for k in ("marketing", "newsletter", "sms", "not a robot")):
                    continue
                el.check(timeout=1500)
                ticked += 1
        except Exception:
            continue
    return ticked


def application_targets(page):
    targets = [page]
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            url = (frame.url or "").lower()
            name = (frame.name or "").lower()
            if any(k in url or k in name for k in (
                "greenhouse", "lever", "ashby", "workday", "smartrecruiters",
                "icims", "application", "grnhse", "job-boards",
            )):
                targets.append(frame)
            elif "job" in url and "google" not in url:
                targets.append(frame)
    except Exception:
        pass
    return targets


def maybe_submit(target, auto_submit: bool) -> bool:
    if not auto_submit:
        return False
    patterns = re.compile(
        r"^(submit application|send application|submit|finish application)$",
        re.I,
    )
    try:
        loc = target.get_by_role("button", name=patterns)
        n = min(loc.count(), 8)
        for i in range(n):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    text = (el.inner_text() or "").strip().lower()
                    if "preview" in text or "save" in text:
                        continue
                    el.click(timeout=4000)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        buttons = target.locator("button, input[type=submit], [role=button]")
        n = min(buttons.count(), 40)
        for i in range(n):
            el = buttons.nth(i)
            try:
                if not el.is_visible():
                    continue
                text = (el.inner_text() or el.get_attribute("value") or "").strip()
                if patterns.search(text) and len(text) < 60:
                    if re.search(r"preview|save for later|cancel", text, re.I):
                        continue
                    el.click(timeout=4000)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _wait(page, ms=900):
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass


def _goto(page, url, wait="domcontentloaded"):
    page.goto(url, wait_until=wait, timeout=45000)
    _wait(page, 1600)


def apply_on_page(page, job_url, profile, resume_path, auto_submit, screenshot_dir: Path, job_id: int):
    context = page.context
    bundle = profile_bundle(profile)
    portal = portal_name(job_url)
    notes = []
    clicked = False
    filled = 0
    uploaded = False
    submitted = False
    block = ""
    active = page

    try:
        _goto(page, job_url)
    except Exception as exc:
        return {
            "clicked_apply": False,
            "fields_filled": 0,
            "resume_uploaded": False,
            "submitted": False,
            "final_url": job_url,
            "page_title": "",
            "screenshot_path": "",
            "portal": portal,
            "block": f"navigation_failed:{exc}",
            "company_guess": company_from_url(job_url),
        }

    dismiss_cookies(page)
    title = ""
    try:
        title = (page.title() or "").strip()[:180]
    except Exception:
        title = ""

    block = detect_block(page)
    apply_direct = apply_url_for(job_url)
    already_apply = apply_direct.rstrip("/") == (page.url or "").split("?")[0].rstrip("/")
    if apply_direct != job_url and portal in ("lever", "ashby") and not already_apply:
        try:
            _goto(page, apply_direct)
            dismiss_cookies(page)
            already_apply = True
            notes.append(f"opened {portal} apply url")
        except Exception:
            notes.append("direct apply url failed")

    popup = None
    if not already_apply:
        try:
            with context.expect_page(timeout=2500) as new_page_info:
                clicked = click_apply(page)
            popup = new_page_info.value
        except Exception:
            clicked = click_apply(page)
            popup = None
    else:
        clicked = True
    if popup:
        try:
            popup.wait_for_load_state("domcontentloaded")
            active = popup
            dismiss_cookies(active)
            notes.append("followed apply popup")
        except Exception:
            pass
    _wait(page, 1200)
    if active != page:
        _wait(active, 800)
    try:
        active.wait_for_selector(
            "form, input[type=email], input[type=file], input[name=name], iframe#grnhse_iframe, iframe[src*='greenhouse'], iframe[src*='lever']",
            timeout=6000,
        )
    except Exception:
        pass

    block = block or detect_block(active)
    targets = application_targets(active)
    if page is not active:
        targets.extend(application_targets(page))

    for target in targets:
        try:
            filled += fill_core_fields(target, bundle)
            if upload_resume(target, resume_path):
                uploaded = True
            tick_required_consents(target)
        except Exception:
            continue

    if filled == 0 and not uploaded and not clicked and apply_direct == job_url:
        clicked = click_apply(active)
        _wait(active, 1400)
        for target in application_targets(active):
            filled += fill_core_fields(target, bundle)
            if upload_resume(target, resume_path):
                uploaded = True
            tick_required_consents(target)

    if auto_submit and (filled or uploaded):
        for target in application_targets(active):
            if maybe_submit(target, True):
                submitted = True
                notes.append("clicked submit")
                break
        _wait(active, 2200)
        if detect_success(active):
            submitted = True
            notes.append("success text seen")

    block = block or detect_block(active)
    shot = Path(screenshot_dir) / f"job-{job_id}.png"
    shot_path = ""
    try:
        active.screenshot(path=str(shot), full_page=True)
        shot_path = str(shot)
    except Exception:
        try:
            page.screenshot(path=str(shot), full_page=True)
            shot_path = str(shot)
        except Exception:
            shot_path = ""

    final_url = ""
    try:
        final_url = active.url
    except Exception:
        final_url = page.url
    try:
        title = title or (active.title() or "")[:180]
    except Exception:
        pass

    if popup and popup != page:
        try:
            popup.close()
        except Exception:
            pass

    return {
        "clicked_apply": bool(clicked),
        "fields_filled": int(filled),
        "resume_uploaded": bool(uploaded),
        "submitted": bool(submitted),
        "final_url": final_url,
        "page_title": title,
        "screenshot_path": shot_path,
        "portal": portal,
        "block": block,
        "company_guess": company_from_url(job_url),
        "notes": "; ".join(notes),
    }
