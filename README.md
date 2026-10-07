# Daily .NET Developer Job Tracker

Runs every day at 9:00 AM (IST by default), pulls .NET Developer jobs,
filters by skills detected in your resume and 1.4 years of experience, and
emails you an Excel file with a "Changes Needed" column.

The current resume matches .NET, C#, ASP.NET Core, SQL, SQL Server,
Entity Framework, Web API, REST, Azure, and Git. The tracker reads the
resume on every run, using `required_skills` as its skill vocabulary.
Skills absent from the resume do not count toward job matching, but can
still appear in "Changes Needed". Missing or unreadable resume text stops
resume-based matching rather than silently using unrelated skills.

Experience requirements such as `1-2 years` and `1+ years` are accepted;
`2 years`, `2+ years`, and `3-6 years` are excluded from all sheets.
Jobs that do not state experience are included by default, but omitted
from the `1.4 Years Experience` sheet. `1.4` means decimal years (about
17 months), not one year and four months; use approximately `1.3333` for
16 months. Experience matching is a best-effort description-text check.

## Why not LinkedIn/Naukri scraping?

LinkedIn actively blocks and legally pursues automated scraping (see the
long-running *hiQ v. LinkedIn* litigation), and Naukri's terms similarly
prohibit it. Scraping them risks your account getting banned and puts you
in a legal gray area. This uses **Adzuna** instead — a free, legitimate job
search API whose listings are sourced from Indeed and hundreds of other
boards, so you get broad coverage without the risk. If you later get access
to official LinkedIn/Naukri recruiter APIs, `main.py`'s `fetch_adzuna_jobs`
function is the only place you'd need to add a second fetch function.

## One-time setup (about 15 minutes)

### 1. Get Adzuna API credentials (free)
- Sign up at https://developer.adzuna.com/
- Copy your `App ID` and `App Key`

### 2. Create a Gmail App Password (free)
- Turn on 2-Step Verification on the Gmail account you'll send *from*:
  https://myaccount.google.com/security
- Create an App Password: https://myaccount.google.com/apppasswords
- Copy the 16-character password

### 3. Add your resume
- Put your resume PDF at `resume/resume.pdf` in this repo.
- **Privacy note:** if this repo is public, your resume will be too. Either
  make the GitHub repo **private** (free for personal repos), or skip
  committing your resume and instead download it into the runner at
  workflow time from a private storage location — ask me if you'd like
  that version instead.

### 4. Push this folder to a new GitHub repository
```bash
cd job_tracker
git init
git add .
git commit -m "Daily job tracker"
git branch -M main
git remote add origin https://github.com/<you>/<repo-name>.git
git push -u origin main
```

### 5. Add secrets to the repo
GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add all five:
| Secret name | Value |
|---|---|
| `ADZUNA_APP_ID` | from step 1 |
| `ADZUNA_APP_KEY` | from step 1 |
| `EMAIL_ADDRESS` | the Gmail address sending the email |
| `EMAIL_APP_PASSWORD` | the app password from step 2 |
| `EMAIL_TO` | the address that should receive the daily update |

### 6. Test it
Go to the **Actions** tab → "Daily .NET Developer Jobs Update" →
**Run workflow** (this uses the `workflow_dispatch` trigger, no need to wait
for 9 AM). Check your email.

From then on it runs automatically every day at 9:00 AM IST.

## Direct company career portals (new)

Beyond Adzuna, the tracker now also queries ~100 Bangalore/India tech
companies directly via their ATS (Greenhouse/Lever/Ashby/SmartRecruiters) —
see `companies.yaml`. These are public, documented job-board APIs, not
scraping — fully ToS-compliant.

**These slugs are best-effort guesses** (built without live network access
to verify). Wrong slugs just return zero jobs for that company — harmless.
After a run, check `data/skipped_companies.txt` in the repo to see which
companies didn't resolve, then fix the slug or remove the entry in
`companies.yaml`. To find a company's correct slug:
- Greenhouse: visit `boards.greenhouse.io/<slug>`
- Lever: visit `jobs.lever.co/<slug>`
- Ashby: visit `jobs.ashbyhq.com/<slug>`
- SmartRecruiters: visit `careers.smartrecruiters.com/<slug>`

Add as many companies as you like — no upper limit.

## No-repeat tracking

`data/seen_jobs.json` stores every job URL that's already been emailed to
you. Each day's workbook separates unseen postings into "New Jobs" and
previously emailed postings into "Old Jobs (Repeated)"; "All Jobs" contains
both. After a successful run, the
workflow commits the updated file back to your repo — this is why the
workflow needs `permissions: contents: write` (already set) and why you'll
see an extra automated commit in your repo history each day. Entries are
auto-pruned after 90 days.

`min_daily_new_jobs` in `config.yaml` (default 10) is your target — if
fewer new jobs match on a given day, the email tells you so, so you know to
loosen `required_skills`/`match_mode` or add more companies. It's a
heads-up, not a guarantee — some days the market just won't have 10 new
matching postings across all sources.

## Customizing

Edit `config.yaml`:
- `search_keywords` — job titles to search
- `locations` — cities/countries (Adzuna country codes: `in`, `us`, `gb`, `ca`, `au`, ...)
- `required_skills` — your exact skill list
- `use_resume_skills` — match only vocabulary skills detected in the PDF
- `core_skills` — mandatory job-description skills (.NET by default, with aliases)
- `match_mode` — `any`, `all`, or `min_count` (with `min_skill_matches`)
- `experience_filter.years` — your experience in decimal years
- `experience_filter.include_unspecified` — include jobs without stated experience
- `job_focus` and `output_filename` — email subject and workbook filename

## Future: Karnataka government notifications

Government notifications are not fetched yet. A later addition can use
official Karnataka recruitment sources such as KPSC and KEA, and report
notification links, application deadlines, qualifications, and age limits
in a separate sheet. These need their own eligibility checks, not the
private-sector .NET skill and experience filter.

To change the run time, edit the `cron:` line in
`.github/workflows/daily-job-update.yml` — GitHub Actions cron is always UTC,
so convert your local 9 AM to UTC.

## Running locally (for testing)
```bash
pip install -r requirements.txt
export ADZUNA_APP_ID=xxx
export ADZUNA_APP_KEY=xxx
export EMAIL_ADDRESS=you@gmail.com
export EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
export EMAIL_TO=you@gmail.com
python main.py
```

Run offline regression tests (no API credentials or email needed):
```bash
python -m unittest test_main -v
```

## Known limitations
- Adzuna's coverage of LinkedIn-exclusive postings is limited — it won't
  catch every LinkedIn-only listing, since LinkedIn doesn't syndicate to
  aggregators. This is the trade-off for staying ToS-compliant.
- The "Changes Needed" column only compares against the `required_skills`
  list in `config.yaml`, not every possible phrase in the job description —
  keep that list as close to your real target skills as possible.
- GitHub Actions free tier includes 2,000 minutes/month for private repos,
  which is far more than this job needs (each run takes under a minute).
