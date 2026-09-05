# Nightshift Job Agent

VM-local agent that opens company career pages in a real browser, finds jobs, fills the application UI, and can submit while you sleep.

It does not use job-board APIs. Playwright drives the websites.

## How it works

1. You add career page URLs (Greenhouse, Lever, Ashby, Workday, or a company `/careers` page).
2. A headless Chromium scan harvests job links from those pages.
3. Jobs are scored against your profile skills and summary.
4. The agent opens each high-score posting, clicks Apply, fills name/email/phone/links, and uploads your resume.
5. Screenshots are saved so you can review what happened.

Dry-run is on by default. The form is filled but Submit is not clicked until you turn off dry-run and enable auto-submit.

## Run on this VM

```bash
# Install Python deps
pip install --break-system-packages -r requirements.txt

# Install Chromium for Playwright
python -m playwright install chromium
python -m playwright install-deps chromium
```

```bash
# Start the control API and dashboard
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open the dashboard, then:

1. Fill Profile and upload a resume.
2. Add company career URLs.
3. Click Run one cycle now and inspect screenshots.
4. Enable overnight loop when you are ready.
5. Only then disable dry-run if you want real submits.

## Limits

Career sites differ. CAPTCHA, logins, and two-factor will stop a cycle. Those jobs land in `needs_review`. Keep `max per night` low.
