"""
Daily .NET Developer Job Tracker
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
SKILL_ALIASES = {
    ".NET": ["dotnet", "dot net", "ASP.NET", "ASP NET", "ASPNet"],
    "C#": ["C Sharp", "CSharp"],
    "ASP.NET Core": ["ASP NET Core", "ASPNet Core"],
    "SQL Server": ["MSSQL", "MS SQL"],
    "Entity Framework": ["EF Core", "EntityFramework"],
    "Web API": ["WebAPI"],
    "REST": ["RESTful"],
}


def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def fetch_adzuna_jobs(app_id, app_key, keyword, country, city, results):
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    # "Remote" and "India" (nationwide) both mean "don't restrict by city" —
    # Adzuna's `where` param wants an actual place name, and country="in"
    # already scopes results to India.
    where = "" if city.lower() in ("remote", "india") else city
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": keyword,
        "where": where,
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
    for name in [skill, *SKILL_ALIASES.get(skill, [])]:
        pattern = r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def get_matching_skills(config, resume_text):
    skills = config["required_skills"]
    if not config.get("use_resume_skills", False):
        return skills
    if not resume_text.strip():
        raise ValueError("Resume-based search requires a readable resume PDF.")
    matched = [skill for skill in skills if skill_found_in_text(skill, resume_text)]
    if not matched:
        raise ValueError("No configured skills found in the resume; update required_skills.")
    return matched


# Matches things like: "3-6 years", "3 to 6 years", "5+ years", "minimum 3 years",
# "at least 4 years", "3 yrs", "2-4 yrs of experience"
EXPERIENCE_PATTERNS = [
    re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*(?:-|\u2013|\u2014|to)\s*(\d{1,2}(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE),
    re.compile(r"(?:minimum|min\.?|at least)\s*(\d{1,2}(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE),
    re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*\+\s*(?:years?|yrs?)\b", re.IGNORECASE),
    re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*(?:years?|yrs?)\b", re.IGNORECASE),
]


def extract_experience_range(text):
    """Returns (min_years, max_years) found in the text, or None if no
    experience mention is found. For open-ended mentions like '5+ years',
    max_years is treated as min_years + 10 (i.e. 'at least 5')."""
    if not text:
        return None
    # range pattern first (most specific)
    m = EXPERIENCE_PATTERNS[0].search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), max(lo, hi))
    # "minimum X years" / "at least X years"
    m = EXPERIENCE_PATTERNS[1].search(text)
    if m:
        lo = float(m.group(1))
        return (lo, lo + 10)
    # "X+ years"
    m = EXPERIENCE_PATTERNS[2].search(text)
    if m:
        lo = float(m.group(1))
        return (lo, lo + 10)
    m = EXPERIENCE_PATTERNS[3].search(text)
    if m:
        lo = float(m.group(1))
        return (lo, lo + 10)
    return None


def experience_overlaps(job_range, target_min, target_max):
    """True if the job's experience range overlaps the target range at all."""
    if job_range is None:
        return False
    job_min, job_max = job_range
    return job_min <= target_max and job_max >= target_min


def filter_jobs_by_experience(jobs, years, include_unspecified=True):
    filtered = []
    for job in jobs:
        job_range = extract_experience_range(job["description"])
        if job_range is None:
            keep = include_unspecified
        else:
            keep = experience_overlaps(job_range, years, years)
        if keep:
            filtered.append(job)
    return filtered


def filter_jobs_by_skills(jobs, required_skills, match_mode, min_matches, core_skills=None):
    core_skills = core_skills or []
    filtered = []
    for job in jobs:
        text = job["description"]

        # Hard gate: every core skill must be present, or the job is dropped
        # regardless of match_mode.
        if core_skills and not all(skill_found_in_text(s, text) for s in core_skills):
            continue

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


def save_excel(sheets, output_filename):
    """sheets: dict of {sheet_name: dataframe}"""
    today = datetime.date.today().isoformat()
    dated_name = output_filename.replace(".xlsx", f"_{today}.xlsx")
    for path in (output_filename, dated_name):
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)
    return output_filename


def send_email(file_path, email_from, email_password, email_to, total_count, new_count, old_count, exp_count, min_target,
               job_focus=".NET Developer", experience_label="1.4 Years Experience"):
    msg = EmailMessage()
    msg["Subject"] = f"Daily {job_focus} Jobs Update"
    msg["From"] = f"Job Tracker Bot <{email_from}>"
    msg["To"] = email_to

    body = (
        f"Attached: {total_count} total matching job(s) today, filtered by your "
        f"resume skills and configured experience, with a 'Changes Needed' "
        f"column showing what to add to your resume for each role.\n\n"
        f"Sheet 1 'All Jobs': all {total_count} matches today.\n"
        f"Sheet 2 'New Jobs': {new_count} you haven't been sent before.\n"
        f"Sheet 3 'Old Jobs (Repeated)': {old_count} sent on a previous day too.\n"
        f"Sheet 4 '{experience_label}': {exp_count} matches with an explicit "
        f"compatible experience requirement. Jobs without a stated requirement "
        f"appear only in the other sheets when enabled.\n"
    )
    if new_count < min_target:
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

    resume_text = extract_resume_text(HERE / config["resume_path"])
    matching_skills = get_matching_skills(config, resume_text)
    print(f"Searching with skills: {', '.join(matching_skills)}")

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
        all_jobs, matching_skills,
        config.get("match_mode", "min_count"),
        config.get("min_skill_matches", 2),
        config.get("core_skills", []),
    )
    print(f"{len(matched_jobs)} jobs matched your skill filter.")

    exp_cfg = config.get("experience_filter", {})
    years = exp_cfg.get("years")
    if years is not None:
        matched_jobs = filter_jobs_by_experience(
            matched_jobs, years, exp_cfg.get("include_unspecified", True),
        )
        print(f"{len(matched_jobs)} jobs compatible with {years} years of experience.")

    seen_jobs = load_seen_jobs()
    old_jobs = [j for j in matched_jobs if j["url"] in seen_jobs]
    new_jobs = [j for j in matched_jobs if j["url"] not in seen_jobs]
    print(f"{len(new_jobs)} new, {len(old_jobs)} already seen on a previous day.")

    min_target = config.get("min_daily_new_jobs", 10)

    if not matched_jobs:
        print("No matching jobs today — skipping email.")
        return

    exp_min = exp_cfg.get("min_years", years if years is not None else 3)
    exp_max = exp_cfg.get("max_years", years if years is not None else 6)
    experience_label = (
        f"{exp_min} Years Experience" if exp_min == exp_max
        else f"{exp_min}-{exp_max} Years Experience"
    )
    experience_jobs = [
        job for job in matched_jobs
        if experience_overlaps(extract_experience_range(job["description"]), exp_min, exp_max)
    ]
    print(f"{len(experience_jobs)} of all matched jobs are in the {exp_min}-{exp_max} years experience range.")

    sheets = {
        "All Jobs": build_dataframe(matched_jobs, resume_text, config["required_skills"]),
        "New Jobs": build_dataframe(new_jobs, resume_text, config["required_skills"]),
        "Old Jobs (Repeated)": build_dataframe(old_jobs, resume_text, config["required_skills"]),
        experience_label: build_dataframe(experience_jobs, resume_text, config["required_skills"]),
    }

    output_path = HERE / config["output_filename"]
    save_excel(sheets, str(output_path))
    print(f"Saved Excel to {output_path} with sheets: {list(sheets.keys())}")

    send_email(str(output_path), email_from, email_password, email_to,
               len(matched_jobs), len(new_jobs), len(old_jobs), len(experience_jobs), min_target,
               config.get("job_focus", ".NET Developer"), experience_label)
    print("Email sent.")

    today = datetime.date.today().isoformat()
    for job in matched_jobs:
        seen_jobs[job["url"]] = today
    save_seen_jobs(seen_jobs)
    print(f"Updated seen_jobs.json ({len(seen_jobs)} total tracked URLs).")


if __name__ == "__main__":
    main()
   
