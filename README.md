# Daily Data Engineering Job Tracker

Runs every day at 9:00 AM (IST by default), pulls Data Engineering jobs,
filters by your required skills, checks each job against your resume, and
emails you an Excel file with a "Changes Needed" column.

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
Go to the **Actions** tab → "Daily Data Engineering Jobs Update" →
**Run workflow** (this uses the `workflow_dispatch` trigger, no need to wait
for 9 AM). Check your email.

From then on it runs automatically every day at 9:00 AM IST.

## Customizing

Edit `config.yaml`:
- `search_keywords` — job titles to search
- `locations` — cities/countries (Adzuna country codes: `in`, `us`, `gb`, `ca`, `au`, ...)
- `required_skills` — your exact skill list
- `match_mode` — `any`, `all`, or `min_count` (with `min_skill_matches`)

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

## Known limitations
- Adzuna's coverage of LinkedIn-exclusive postings is limited — it won't
  catch every LinkedIn-only listing, since LinkedIn doesn't syndicate to
  aggregators. This is the trade-off for staying ToS-compliant.
- The "Changes Needed" column only compares against the `required_skills`
  list in `config.yaml`, not every possible phrase in the job description —
  keep that list as close to your real target skills as possible.
- GitHub Actions free tier includes 2,000 minutes/month for private repos,
  which is far more than this job needs (each run takes under a minute).
