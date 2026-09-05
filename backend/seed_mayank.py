import json
import shutil
from pathlib import Path

from backend.db import UPLOADS, connect, init_db, log

RESUME_SRC = Path(
    "/workspace/.monkeycode-tmp-files/4544827d-Mayank_Mittal_QA_Automation_Resume-1.docx"
)
RESUME_DST = UPLOADS / "Mayank_Mittal_QA_Automation_Resume.docx"

ANSWERS = {
    "current_location": "Greater Noida West, NCR",
    "preferred_location": "NCR or Remote India",
    "expected_ctc": "40+ LPA",
    "current_ctc": "",
    "notice_period": "As per offer discussion",
    "total_experience": "12+ years",
    "open_to_remote": "Yes",
    "open_to_hybrid_ncr": "Yes",
    "work_authorization": "Indian citizen, valid passport",
    "primary_stack": "Java, Selenium, REST Assured, Playwright, Appium, Cucumber, TestNG",
}

COMPANIES = [
    ("BrowserStack", "https://www.browserstack.com/careers"),
    ("LambdaTest", "https://www.lambdatest.com/careers"),
    ("Atlassian", "https://www.atlassian.com/company/careers/all-jobs"),
    ("Uber", "https://www.uber.com/careers/list/"),
    ("Adobe", "https://careers.adobe.com/us/en/search-results"),
    ("Intuit", "https://jobs.intuit.com/"),
    ("Salesforce", "https://careers.salesforce.com/en/jobs/"),
    ("Thoughtworks", "https://www.thoughtworks.com/careers/jobs"),
    ("PhonePe", "https://www.phonepe.com/careers/"),
    ("Razorpay", "https://razorpay.com/jobs/"),
    ("Freshworks", "https://www.freshworks.com/company/careers/"),
    ("Sprinklr", "https://www.sprinklr.com/careers/"),
    ("EPAM", "https://www.epam.com/careers"),
    ("Publicis Sapient", "https://careers.publicissapient.com/jobs"),
    ("Swiggy", "https://careers.swiggy.com/"),
    ("Zomato", "https://www.zomato.com/careers"),
    ("Groww", "https://groww.in/careers"),
    ("CRED", "https://careers.cred.club/"),
]


def seed():
    conn = connect()
    init_db(conn)
    if RESUME_SRC.exists():
        shutil.copyfile(RESUME_SRC, RESUME_DST)
        resume_path = str(RESUME_DST)
    else:
        resume_path = ""

    conn.execute(
        """UPDATE profile SET full_name=?, email=?, phone=?, website=?, location=?,
           skills=?, summary=?, resume_path=?, answers_json=? WHERE id=1""",
        (
            "Mayank Mittal",
            "mayankmittal2018@gmail.com",
            "+91 9767968418",
            "https://www.onlinejavacompiler.online",
            "Greater Noida West, NCR / Remote India",
            "Java, Selenium, Selenium WebDriver, Appium, Playwright, REST Assured, Postman, "
            "Cucumber, BDD, TestNG, JUnit, Maven, Jenkins, GitHub Actions, API testing, "
            "mobile testing, SDET, QA Automation, JIRA, MySQL, Agile, POM",
            "QA Automation Lead with 12+ years in Java, Selenium, Appium, Playwright and API testing. "
            "Seeking senior SDET / QA Automation Lead roles, remote or NCR, 40+ LPA. "
            "Banking, FinTech, SaaS and eCommerce automation frameworks, CI/CD, client-facing lead.",
            resume_path,
            json.dumps(ANSWERS),
        ),
    )
    conn.execute(
        """UPDATE settings SET dry_run=1, auto_submit=0, overnight_enabled=0,
           min_score=40, max_per_night=6, interval_minutes=30 WHERE id=1"""
    )
    existing = {
        r[0] for r in conn.execute("SELECT career_url FROM companies").fetchall()
    }
    for name, url in COMPANIES:
        if url in existing:
            continue
        conn.execute(
            "INSERT INTO companies (name, career_url, enabled, notes) VALUES (?, ?, 1, ?)",
            (name, url, "NCR/remote SDET target"),
        )
    conn.commit()
    log(conn, "Seeded Mayank Mittal profile, resume, and target career pages")
    conn.close()


if __name__ == "__main__":
    seed()
