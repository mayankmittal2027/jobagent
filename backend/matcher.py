import re

STOP = {
    "and", "or", "the", "a", "an", "to", "of", "in", "for", "with", "on",
    "at", "by", "from", "as", "is", "are", "be", "this", "that", "we",
    "you", "our", "your", "job", "role", "team", "work", "experience",
}

TARGET_TITLES = (
    "sdet", "qa automation", "automation test", "test automation",
    "automation engineer", "quality engineer", "software development engineer in test",
    "qa lead", "automation lead", "test lead", "qa engineer", "quality assurance",
    "selenium", "playwright", "appium",
)

JUNIOR = (
    "intern", "internship", "graduate", "fresher", "entry level", "entry-level",
    "junior", "0-2 years", "1-3 years", "associate qa",
)

WRONG_TRACK = (
    "manual tester only", "manual qa only", "sales", "recruiter", "hr intern",
    "data scientist", "android developer", "ios developer", "frontend developer",
    "product manager", "business analyst",
)

NCR = (
    "noida", "greater noida", "gurgaon", "gurugram", "delhi", "ncr",
    "new delhi", "faridabad", "ghaziabad", "india remote", "remote india",
    "remote - india", "work from home", "wfh", "hybrid",
)

HIGH_PAY = (
    "40 lpa", "40+ lpa", "45 lpa", "50 lpa", "60 lpa", "lakhs",
    "lakh", "$80", "$90", "$100", "$120", "usd 80", "usd 90", "usd 100",
)


def tokens(text: str):
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}", (text or "").lower())
    return {w for w in words if w not in STOP and len(w) > 1}


def _hay(title, description, location):
    return f"{title} {description} {location}".lower()


def score_job(profile: dict, title: str, description: str, location: str) -> float:
    hay = _hay(title, description, location)
    title_l = (title or "").lower()
    loc = (location or "").lower()

    if any(k in hay for k in JUNIOR):
        return 5.0
    if any(k in hay for k in WRONG_TRACK) and not any(k in title_l for k in TARGET_TITLES):
        return 8.0

    skills = [s.strip().lower() for s in (profile.get("skills") or "").split(",") if s.strip()]
    if not skills:
        base = 20.0
    else:
        hits = sum(1 for s in skills if s in hay)
        base = (hits / max(len(skills), 1)) * 55.0

    if any(k in title_l for k in TARGET_TITLES):
        base += 22
    elif any(k in hay for k in TARGET_TITLES):
        base += 12

    if any(k in title_l for k in ("lead", "senior", "staff", "principal", "sdet ii", "sdet 2", "sdet iii")):
        base += 10

    pref_loc = (profile.get("location") or "").lower()
    if any(k in loc or k in hay for k in NCR) or any(k in loc for k in pref_loc.split(",") if k.strip()):
        base += 12
    if "remote" in loc or "remote" in hay:
        base += 10
    if any(k in hay for k in ("on-site us", "must be in usa", "us only", "united states only", "need h1b")):
        base -= 25

    if any(k in hay for k in HIGH_PAY):
        base += 8
    years = re.search(r"(\d+)\+?\s*(?:years|yrs)", hay)
    if years:
        n = int(years.group(1))
        if n >= 8:
            base += 8
        elif n <= 3:
            base -= 20

    summary_toks = tokens(profile.get("summary") or "")
    job_toks = tokens(f"{title} {description}")
    if summary_toks and job_toks:
        overlap = len(summary_toks & job_toks) / max(len(summary_toks), 1)
        base += min(12.0, overlap * 30.0)

    return round(min(100.0, max(0.0, base)), 1)
