# JobRadar 📡

Entry-level (fresher / 0–1 yr) AI/ML, GenAI, software, and data engineering jobs in Delhi NCR,
Bengaluru, Mumbai, Pune, Kolkata, and remote. The list refreshes itself at **12:00 AM and
12:00 PM IST**. The dashboard is split into **LinkedIn Easy Apply**, **Company site**,
**Job boards**, and **HN & Reddit**, with outreach drafts and your own counters.

It runs free on GitHub Actions and GitHub Pages. OpenAI costs are about a few cents per refresh.

## Setup (one time, about 15 minutes)

**1. Create the repo.** On github.com, go to New repository, name it `jobradar`, and make it
**Public** (GitHub Pages is free for public repos). Then click "uploading an existing file", drag
in everything from this folder, and Commit.
   - The `.github` folder is hidden on some systems. Make sure `.github/workflows/refresh.yml`
     actually got uploaded; if not, create that file on GitHub and paste its contents.

**2. Add your profile.** Either edit `profile.md` on GitHub, or keep it private by leaving the
file alone and doing step 3's `CANDIDATE_PROFILE` secret instead.

**3. Add secrets.** Go to Settings → Secrets and variables → Actions → New repository secret.
- `OPENAI_API_KEY` is required for match scores and message drafts.
- `CANDIDATE_PROFILE` is optional: paste your whole profile/CV text here to keep it private.
- `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` are optional.

**4. Allow the bot to save data.** Go to Settings → Actions → General → Workflow permissions,
select "Read and write permissions", and click Save.

**5. Turn on the website.** Go to Settings → Pages → Build and deployment → Source, and choose
**GitHub Actions**. (Not "Deploy from a branch".)

**6. First run.** Go to the Actions tab. If asked, click "I understand my workflows, go ahead
and enable them". Then click Refresh jobs → Run workflow → Run workflow. It takes about
10–20 minutes. When it turns green, the "deploy" step shows your link:
`https://<your-username>.github.io/jobradar/`. Bookmark it.

**7. Check Source health** at the bottom of the dashboard. Every source shows ok or the reason
it failed. Fix or remove failing company URLs or sites in `config.yaml`, which you can edit
directly on GitHub.

After that it refreshes itself at 12:00 AM and 12:00 PM IST. To refresh sooner, repeat step 6.

### Adding more job sites (no code)

Add an entry under `sites:` in `config.yaml`:

```yaml
  - name: MySite
    mode: html            # html = normal site, browser = JavaScript site, rss = blog feed
    list_urls: [https://example.com/jobs?exp=fresher]
    link_pattern: 'example\.com/job/[a-z0-9-]+'   # what a job link looks like
    pages: 2
    page_format: "{url}&page={n}"
    default_location: India
```

To find `link_pattern`, open one job on that site and copy the part of its URL that every job
shares. For WordPress blogs, try `https://site.com/feed/` with `mode: rss`.

### Run on your own computer (optional)

```bash
pip install -r requirements.txt
python -m playwright install chromium
set OPENAI_API_KEY=sk-...        # macOS/Linux: export OPENAI_API_KEY=sk-...
python -m jobradar.run           # full refresh; writes docs/data/
python -m http.server -d docs 8000   # open http://localhost:8000
```

Useful flags:
- `--no-ai` skips OpenAI.
- `--only sites,companies,linkedin,indeed,naukri,remote_boards,hackernews,reddit` runs just
  those sources.

## Daily use (about 15 seconds per job)

1. Open the dashboard. **Orange dots** mark jobs that are new this refresh. Start with the
   **LinkedIn Easy Apply** tab, sorted by **Best match**.
2. Click **Apply ↗**, apply on LinkedIn, then click **Mark applied**.
3. Expand the row and use **Find people → Recruiters in my network**. This opens a LinkedIn
   search for 1st/2nd-degree recruiters at that company.
4. Type the person's first name. The draft fills it in. **Copy**, send it yourself on LinkedIn,
   then click **Mark sent**.
   - **Connection note** (≤200 characters): LinkedIn free accounts get only a few personalized
     notes a month, so save these for your top companies. Otherwise connect without a note.
   - **Message**: send this once they accept.
   - **Email**: use this if you have an address. **Open in mail app** pre-fills it.
5. **Copy JD** sends the job description to your clipboard for CV Tailor.

Your counters (applied through Easy Apply, applied on company sites, messages sent) live in your
browser. Use **Backup your tracking** to export them or move them to your phone.

## What each piece does

| Stage | Details |
|---|---|
| Sources | LinkedIn public job search (logged out, entry-level filter), Indeed India + Naukri (JobSpy), company careers via Greenhouse/Lever/Ashby/SmartRecruiters/Workday APIs, RemoteOK, Remotive, Himalayas, We Work Remotely, HN "Who is hiring?", Reddit, plus any site under `sites:`: Astra (useastra.in), Jobgether, Hirist, engineerHUB, Jobs24x, OffCampusJobs4u, FreshersDunia, EnggWave |
| Rules | Role regex, seniority exclusions, experience parsing ("0–1 yrs", "freshers", "3+ years"…), city/remote matching, max age |
| Easy Apply check | Opens each new LinkedIn job page: if it has an external apply link → **Company site**, otherwise → **Easy Apply** |
| AI | Classifies relevance, experience, and remote eligibility, then gives a 0–100 score with a reason. It only runs on new jobs, and results are cached |
| Drafts | Written for jobs scoring ≥ 70. The system checks company, role, length, placeholders, and clichés, then an AI reviewer checks claims against your profile. Drafts that fail are rewritten once; if they still fail, they're flagged "review before sending" |
| Store | `docs/data/*.json`, committed each run. Duplicates across sources are merged into one row |

Edit `config.yaml` to change:
- roles, experience, and cities;
- LinkedIn search terms;
- company careers URLs (just paste the URL; the ATS is detected automatically).

## Deliberately not automated

JobRadar never logs into LinkedIn, never clicks Apply, and never sends messages. LinkedIn's User
Agreement (section 8.2) bans bots for all of these, and restrictions hit hard in 2026. Every
Apply and Send is your click. That keeps your account safe and keeps your messages human.

## Known limits

- **LinkedIn / Indeed / Naukri may block GitHub's servers** on some runs (HTTP 429/403). Company
  APIs and remote boards are unaffected, and the next run usually recovers. Check **Source
  health** at the bottom of the dashboard.
- **Site-specific notes:**
  - Hirist loads its listings with JavaScript, so it runs in a headless browser; this adds about
    a minute.
  - engineerHUB may require a login. If Source health says "0 job links found", set
    `enabled: false` for it.
  - Jobs24x shows titles publicly, but its apply links need their paid plan.
  - Astra's apply button needs a free Astra login.
- **Company URLs in `config.yaml` are a starter list.** Any that fail show up in Source health;
  fix or remove them, and add companies you care about.
- **Easy Apply detection** depends on LinkedIn's public page. If the page couldn't be read, the job
  shows "apply type unchecked".
- **Experience** is often unstated. Those jobs are kept and judged by the AI, tagged "Exp not
  stated".
- **GitHub's scheduler** can run a few minutes to an hour late. It may pause scheduled runs after
  60 days with no repo activity; if so, click **Enable** in the Actions tab.
- **The repo is public**, so the job list and drafts on the site are visible to anyone with the
  link. Keep your profile in the `CANDIDATE_PROFILE` secret if you prefer.

## Local run and tests

```bash
pip install -r requirements.txt
python -m jobradar.run --no-ai --only companies,remote_boards   # quick smoke test
python tests/test_parsers.py && python tests/test_filters.py && python tests/test_sites.py && python tests/test_run_offline.py
```
