"""
Daily Data Engineering Job Tracker
-----------------------------------
1. Pulls jobs from TWO legitimate sources:
   a) Adzuna API (broad aggregator, includes Indeed-sourced listings)
   b) Direct company career-portal APIs (Greenhouse/Lever/Ashby/SmartRecruiters)
      for companies listed in companies.yaml
2. Filters jobs by your required skill set.
3. Skips any job you've already been sent before (data/seen_jobs.json).
4. Compares each new job description against your resume to find missing
   skills/phrases.
5. Writes results to an Excel file.
6. Emails the Excel file to you, and updates data/seen_jobs.json so tomorrow's
   run won't repeat today's jobs.

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
import json
import sys
import smtplib
import datetime
from email.message import EmailMessage
from pathlib import Path

import yaml
import requests
import pandas as pd
import pdfplumber

from ats_sources import fetch_all_companies

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.yaml"
COMPANIES_PATH = HERE / "companies.yaml"
SEEN_JOBS_PATH = HERE / "data" / "seen_jobs.json"
SKIPPED_LOG_PATH = HERE / "data" / "skipped_companies.txt"
SEEN_JOBS_RETENTION_DAYS = 90


def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def fetch_adzuna_jobs(app_id, app_key, keyword, country, city, results):
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


def collect_adzuna_jobs(config, app_id, app_key):
    all_jobs = []
    for keyword in config["search_keywords"]:
        for loc in config["locations"]:
            jobs = fetch_adzuna_jobs(
                app_id, app_key, keyword,
                loc["country"], loc["city"],
                config.get("results_per_search", 20),
            )
            all_jobs.extend(jobs)
    return all_jobs


def dedupe_by_url(jobs):
    seen = set()
    unique = []
    for j in jobs:
        if j["url"] and j["url"] not in seen:
            seen.add(j["url"])
            unique.append(j)
    return unique


def skill_found_in_text(skill, text):
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


def load_seen_jobs():
    if not SEEN_JOBS_PATH.exists():
        return {}
    try:
        with open(SEEN_JOBS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen_jobs(seen_dict):
    SEEN_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.date.today() - datetime.timedelta(days=SEEN_JOBS_RETENTION_DAYS)).isoformat()
    pruned = {url: date for url, date in seen_dict.items() if date >= cutoff}
    with open(SEEN_JOBS_PATH, "w") as f:
        json.dump(pruned, f, indent=2)


def remove_already_seen(jobs, seen_dict):
    return [j for j in jobs if j["url"] not in seen_dict]


def extract_resume_text(resume_path):
    if not Path(resume_path).exists():
        print(f"[WARN] Resume not found at {resume_path}.", file=sys.stderr)
        return ""
    text = []
    with pdfplumber.open(resume_path) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)


def compute_changes_needed(job, resume_text, required_skills):
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


def send_email(file_path, email_from, email_password, email_to, job_count, min_target):
    msg = EmailMessage()
    msg["Subject"] = "Daily Data Engineering Jobs Update"
    msg["From"] = email_from
    msg["To"] = email_to

    body = (
        f"Attached: {job_count} NEW job(s) today (already-seen jobs from "
        f"previous days are excluded), filtered by your required skills, "
        f"with a 'Changes Needed' column showing what to add to your resume "
        f"for each role.\n"
    )
    if job_count < min_target:
        body += (
            f"\nNote: fewer than your target of {min_target} new roles matched "
            f"today. Consider widening required_skills/match_mode in config.yaml "
            f"or adding more companies to companies.yaml if this happens often.\n"
        )
    msg.set_content(body)

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
    config = load_yaml(CONFIG_PATH)
    companies = load_yaml(COMPANIES_PATH).get("companies", [])

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

    print("Fetching jobs from Adzuna...")
    adzuna_jobs = collect_adzuna_jobs(config, app_id, app_key)
    print(f"  {len(adzuna_jobs)} jobs from Adzuna.")

    print(f"Fetching jobs from {len(companies)} company career portals...")
    company_jobs, skipped = fetch_all_companies(companies)
    print(f"  {len(company_jobs)} jobs from direct company boards.")
    if skipped:
        SEEN_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SKIPPED_LOG_PATH, "w") as f:
            f.write("\n".join(skipped))
        print(f"  {len(skipped)} companies skipped/failed — see data/skipped_companies.txt")

    all_jobs = dedupe_by_url(adzuna_jobs + company_jobs)
    print(f"{len(all_jobs)} unique jobs total before filtering.")

    matched_jobs = filter_jobs_by_skills(
        all_jobs, config["required_skills"],
        config.get("match_mode", "min_count"),
        config.get("min_skill_matches", 2),
    )
    print(f"{len(matched_jobs)} jobs matched your skill filter.")

    seen_jobs = load_seen_jobs()
    new_jobs = remove_already_seen(matched_jobs, seen_jobs)
    print(f"{len(new_jobs)} are NEW (not sent on a previous day).")

    min_target = config.get("min_daily_new_jobs", 10)

    if not new_jobs:
        print("No new matching jobs today — skipping email.")
        return

    resume_text = extract_resume_text(HERE / config["resume_path"])
    df = build_dataframe(new_jobs, resume_text, config["required_skills"])

    output_path = HERE / config["output_filename"]
    save_excel(df, str(output_path))
    print(f"Saved Excel to {output_path}")

    send_email(str(output_path), email_from, email_password, email_to,
               len(new_jobs), min_target)
    print("Email sent.")

    today = datetime.date.today().isoformat()
    for job in new_jobs:
        seen_jobs[job["url"]] = today
    save_seen_jobs(seen_jobs)
    print(f"Updated seen_jobs.json ({len(seen_jobs)} total tracked URLs).")


if __name__ == "__main__":
    main()
