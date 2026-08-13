"""
Daily Data Engineering Job Tracker
-----------------------------------
1. Pulls jobs from the Adzuna API (legitimate, ToS-compliant job aggregator
   that includes listings from Indeed and many other boards).
2. Filters jobs by your required skill set.
3. Compares each job description against your resume to find missing
   skills/phrases.
4. Writes results to an Excel file.
5. Emails the Excel file to you.

Run manually:      python main.py
Run on schedule:    see .github/workflows/daily-job-update.yml

Required environment variables (set as GitHub Secrets, or in a local .env):
  ADZUNA_APP_ID       - from https://developer.adzuna.com/
  ADZUNA_APP_KEY
  EMAIL_ADDRESS       - the Gmail address to SEND from
  EMAIL_APP_PASSWORD  - a Gmail "App Password" (not your normal password)
  EMAIL_TO            - the address to RECEIVE the daily update
"""

import os
import re
import sys
import smtplib
import datetime
from email.message import EmailMessage
from pathlib import Path

import yaml
import requests
import pandas as pd
import pdfplumber

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.yaml"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def fetch_adzuna_jobs(app_id, app_key, keyword, country, city, results):
    """Query Adzuna's job search API. Docs: https://developer.adzuna.com/docs/search"""
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": keyword,
        "where": city if city.lower() != "remote" else "",
        "results_per_page": results,
        "content-type": "application/json",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[WARN] Adzuna request failed for '{keyword}' in {city}: {e}", file=sys.stderr)
        return []

    jobs = []
    for item in data.get("results", []):
        jobs.append({
            "title": item.get("title", "").strip(),
            "company": (item.get("company") or {}).get("display_name", "Unknown"),
            "location": (item.get("location") or {}).get("display_name", city),
            "url": item.get("redirect_url", ""),
            "description": item.get("description", ""),
        })
    return jobs


def collect_all_jobs(config, app_id, app_key):
    all_jobs = []
    for keyword in config["search_keywords"]:
        for loc in config["locations"]:
            jobs = fetch_adzuna_jobs(
                app_id, app_key, keyword,
                loc["country"], loc["city"],
                config.get("results_per_search", 20),
            )
            all_jobs.extend(jobs)

    # de-duplicate by job URL
    seen = set()
    unique_jobs = []
    for j in all_jobs:
        if j["url"] and j["url"] not in seen:
            seen.add(j["url"])
            unique_jobs.append(j)
    return unique_jobs


def skill_found_in_text(skill, text):
    """Whole-word, case-insensitive match so 'R' doesn't match inside 'AWS'."""
    pattern = r"(?<![A-Za-z0-9])" + re.escape(skill) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def filter_jobs_by_skills(jobs, required_skills, match_mode, min_matches):
    filtered = []
    for job in jobs:
        text = job["description"]
        matched = [s for s in required_skills if skill_found_in_text(s, text)]
        if match_mode == "any" and len(matched) >= 1:
            keep = True
        elif match_mode == "all" and len(matched) == len(required_skills):
            keep = True
        elif match_mode == "min_count" and len(matched) >= min_matches:
            keep = True
        else:
            keep = False
        if keep:
            job["matched_skills"] = matched
            filtered.append(job)
    return filtered


def extract_resume_text(resume_path):
    if not Path(resume_path).exists():
        print(f"[WARN] Resume not found at {resume_path}. "
              f"'Changes Needed' will list all matched skills as missing.", file=sys.stderr)
        return ""
    text = []
    with pdfplumber.open(resume_path) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)


def compute_changes_needed(job, resume_text, required_skills):
    """For skills the job cares about (matched_skills, plus any required
    skill mentioned in the JD), flag the ones missing from the resume."""
    missing = []
    for skill in required_skills:
        in_job = skill_found_in_text(skill, job["description"])
        in_resume = skill_found_in_text(skill, resume_text) if resume_text else False
        if in_job and not in_resume:
            missing.append(skill)
    return ", ".join(missing) if missing else "None — resume already covers matched skills"


def build_dataframe(jobs, resume_text, required_skills):
    rows = []
    for job in jobs:
        rows.append({
            "Job Title": job["title"],
            "Company": job["company"],
            "Location": job["location"],
            "Job Link": job["url"],
            "Changes Needed": compute_changes_needed(job, resume_text, required_skills),
        })
    return pd.DataFrame(rows, columns=["Job Title", "Company", "Location", "Job Link", "Changes Needed"])


def save_excel(df, output_filename):
    today = datetime.date.today().isoformat()
    dated_name = output_filename.replace(".xlsx", f"_{today}.xlsx")
    df.to_excel(output_filename, index=False)
    df.to_excel(dated_name, index=False)
    return output_filename


def send_email(file_path, email_from, email_password, email_to):
    msg = EmailMessage()
    msg["Subject"] = "Daily Data Engineering Jobs Update"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(
        "Attached is today's Data Engineering job list, filtered by your "
        "required skills, with a 'Changes Needed' column showing what to "
        "add to your resume for each role."
    )
    with open(file_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=Path(file_path).name,
        )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(email_from, email_password)
        smtp.send_message(msg)


def main():
    config = load_config()

    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    email_from = os.environ.get("EMAIL_ADDRESS")
    email_password = os.environ.get("EMAIL_APP_PASSWORD")
    email_to = os.environ.get("EMAIL_TO")

    missing_env = [name for name, val in [
        ("ADZUNA_APP_ID", app_id), ("ADZUNA_APP_KEY", app_key),
        ("EMAIL_ADDRESS", email_from), ("EMAIL_APP_PASSWORD", email_password),
        ("EMAIL_TO", email_to),
    ] if not val]
    if missing_env:
        print(f"[ERROR] Missing required environment variables: {missing_env}", file=sys.stderr)
        sys.exit(1)

    print("Fetching jobs...")
    jobs = collect_all_jobs(config, app_id, app_key)
    print(f"  {len(jobs)} unique jobs found before filtering.")

    jobs = filter_jobs_by_skills(
        jobs, config["required_skills"],
        config.get("match_mode", "min_count"),
        config.get("min_skill_matches", 2),
    )
    print(f"  {len(jobs)} jobs matched your skill filter.")

    if not jobs:
        print("No matching jobs today — skipping email.")
        return

    resume_text = extract_resume_text(HERE / config["resume_path"])
    df = build_dataframe(jobs, resume_text, config["required_skills"])

    output_path = HERE / config["output_filename"]
    save_excel(df, str(output_path))
    print(f"Saved Excel to {output_path}")

    send_email(str(output_path), email_from, email_password, email_to)
    print("Email sent.")


if __name__ == "__main__":
    main()
