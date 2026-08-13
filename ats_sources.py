"""
Fetchers for company career-portal APIs (ATS platforms).

These are all public, documented-by-convention JSON endpoints that the ATS
providers themselves expose so job boards are machine-readable — this is
different from scraping LinkedIn/Naukri HTML, which those sites explicitly
prohibit. If a slug is wrong (company renamed/moved), the request either
404s or returns an empty list — both handled gracefully, no crash.
"""

import sys
import requests

TIMEOUT = 15


def fetch_greenhouse(company_name, slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return [], f"{company_name} (greenhouse/{slug}): HTTP {resp.status_code}"
        data = resp.json()
        jobs = []
        for item in data.get("jobs", []):
            jobs.append({
                "title": item.get("title", "").strip(),
                "company": company_name,
                "location": (item.get("location") or {}).get("name", "N/A"),
                "url": item.get("absolute_url", ""),
                "description": item.get("content", "") or "",
            })
        return jobs, None
    except requests.RequestException as e:
        return [], f"{company_name} (greenhouse/{slug}): {e}"


def fetch_lever(company_name, slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return [], f"{company_name} (lever/{slug}): HTTP {resp.status_code}"
        data = resp.json()
        jobs = []
        for item in data:
            jobs.append({
                "title": item.get("text", "").strip(),
                "company": company_name,
                "location": (item.get("categories") or {}).get("location", "N/A"),
                "url": item.get("hostedUrl", ""),
                "description": (item.get("descriptionPlain") or item.get("description") or ""),
            })
        return jobs, None
    except requests.RequestException as e:
        return [], f"{company_name} (lever/{slug}): {e}"


def fetch_ashby(company_name, slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return [], f"{company_name} (ashby/{slug}): HTTP {resp.status_code}"
        data = resp.json()
        jobs = []
        for item in data.get("jobs", []):
            jobs.append({
                "title": item.get("title", "").strip(),
                "company": company_name,
                "location": item.get("location", "N/A"),
                "url": item.get("jobUrl", ""),
                "description": item.get("descriptionPlain", "") or item.get("description", ""),
            })
        return jobs, None
    except requests.RequestException as e:
        return [], f"{company_name} (ashby/{slug}): {e}"


def fetch_smartrecruiters(company_name, slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return [], f"{company_name} (smartrecruiters/{slug}): HTTP {resp.status_code}"
        data = resp.json()
        jobs = []
        for item in data.get("content", []):
            job_id = item.get("id", "")
            loc = item.get("location", {})
            location = ", ".join(filter(None, [loc.get("city"), loc.get("country")])) or "N/A"
            detail_url = f"https://jobs.smartrecruiters.com/{slug}/{job_id}"
            jobs.append({
                "title": item.get("name", "").strip(),
                "company": company_name,
                "location": location,
                "url": item.get("ref", detail_url) or detail_url,
                # SmartRecruiters list endpoint doesn't include full description;
                # skill-matching falls back to the job title for this provider.
                "description": item.get("name", ""),
            })
        return jobs, None
    except requests.RequestException as e:
        return [], f"{company_name} (smartrecruiters/{slug}): {e}"


PROVIDER_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
}


def fetch_all_companies(companies):
    """companies: list of {name, provider, slug} dicts from companies.yaml"""
    all_jobs = []
    skipped = []
    for c in companies:
        fetcher = PROVIDER_FETCHERS.get(c["provider"])
        if not fetcher:
            skipped.append(f"{c['name']}: unknown provider '{c['provider']}'")
            continue
        jobs, error = fetcher(c["name"], c["slug"])
        if error:
            skipped.append(error)
        all_jobs.extend(jobs)
    return all_jobs, skipped
