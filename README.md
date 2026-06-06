# Intern Watch Agent 🤖

Monitors public job sources for **new ML/AI engineer internships** (default: **Summer 2027, US**)
and pings you on **Telegram** when fresh roles appear. Built for a UW CS junior, but the
target list is fully configurable.

Zero third-party Python packages — uses the standard library only. Needs Python 3.9+.

---

## How it works

```
 aggregators (listings.json)  ─┐
 Greenhouse APIs  ─────────────┤→ filter (ML/AI ∧ intern ∧ season/year ∧ US)
 Ashby / Lever APIs  ──────────┘        → de-dupe across sources
                                        → diff vs state/seen.json  (only NEW roles)
                                        → Telegram notification
                                        → persist state
```

- **Aggregators** — community-maintained `listings.json` feeds (SimplifyJobs, vanshb03, cvrve)
  cover hundreds of companies. The **Summer 2027** editions go live around mid-2026; until then
  those URLs 404 and are skipped automatically (the 2026 URLs are included so the pipeline is
  testable today).
- **ATS APIs** — direct polls of each target company's Greenhouse/Ashby/Lever board catch roles
  the instant they post. The seeded slugs were all verified live.

Any source that errors (404, timeout) is logged and skipped — one bad source never blocks a run.

> **No accounts, ever.** Every source is a *public, unauthenticated* endpoint — the same JSON a
> company's own careers page loads in your browser (Greenhouse, Ashby, Workday CXS, `amazon.jobs`,
> the community feeds). The agent never logs in anywhere, so **there is no account to lock**; the
> worst case is a temporary IP rate-limit, which fails safe and the health-watchdog flags. The one
> optional exception, USAJobs, uses a *free public-data API key* (not a personal login).
> LinkedIn and Indeed are deliberately **not** used (no usable public API; scraping gets blocked).

---

## Setup (one time)

### 1. Create a Telegram bot & get your chat id
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the **bot token**
   (looks like `123456789:AAE...`).
2. Open a chat with your new bot and send it any message (e.g. "hi") — this is required so the
   bot can message you back.
3. Get your **chat id**: message **@userinfobot** (it replies with your numeric id), *or* visit
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read `chat.id`.

### 2. Add credentials
Put them in `config.json`:
```json
"telegram": { "bot_token": "123456789:AAE...", "chat_id": "987654321" }
```
…or keep them out of the file via environment variables (these override config.json):
```powershell
$env:TELEGRAM_BOT_TOKEN = "123456789:AAE..."
$env:TELEGRAM_CHAT_ID   = "987654321"
```

### 3. Verify, baseline, then schedule
```powershell
cd c:\Beanworks\Agents\InternJobs\intern_agent

python agent.py --test-notify   # 1) confirm you receive the Telegram test message
python agent.py --seed          # 2) baseline: marks everything currently open as "seen"
                                #    so your first live run won't dump the whole backlog
python agent.py --dry-run       # 3) (optional) preview what a run would flag as new

.\register_task.ps1             # 4) schedule it (daily 08:30 by default)
# e.g. twice a day:  .\register_task.ps1 -Times "08:00","20:00"
```
From then on it runs automatically and you get a Telegram ping only when **new** matching roles
appear.

> **Note on timing:** right now (mid-2026) almost no Summer-2027 intern roles exist yet, so runs
> will usually report `0 new`. That's expected — the agent is waiting. The Summer-2027 aggregator
> feeds switch on around Aug–Oct 2026; the ATS polls work today.

---

## ☁️ Run in the cloud (GitHub Actions) — works even when your PC is off

This repo includes `.github/workflows/intern-watch.yml`, which runs the agent on GitHub's
servers on a schedule. No dependency on your laptop being awake. State (`state/seen.json`)
is committed back after every run, so the cloud remembers what it already told you about.

**Secrets never live in the repo** — `config.json` holds only the company lists/filters.
The token + chat id come from GitHub Actions Secrets in the cloud, and from a gitignored
`secrets.json` (or env vars) locally.

### One-time setup
1. Push this folder to your repo:
   ```bash
   git init -b main
   git add -A
   git commit -m "intern watch agent"
   git remote add origin https://github.com/OLorents/intern_agent.git
   git push -u origin main
   ```
2. Add the two secrets: **Repo → Settings → Secrets and variables → Actions → New repository secret**
   - `TELEGRAM_BOT_TOKEN` = your BotFather token
   - `TELEGRAM_CHAT_ID`   = your numeric chat id (`611851983`)
3. **Actions** tab → enable workflows if prompted → open **Intern Watch** → **Run workflow** to test now.

### Schedule & caveats
- Runs twice daily at **15:00 & 03:00 UTC** (≈ 08:00 & 20:00 US Pacific in summer, one hour
  earlier in winter). Edit the `cron:` lines to change. Manual runs anytime from the Actions tab.
- GitHub may delay scheduled runs a few minutes under load — harmless here.
- The workflow **fails loudly** if the Telegram secrets are missing (instead of silently never
  notifying), and pushes state with `git pull --rebase` + retry so the two daily runs can't
  clobber each other's state.
- Uses **no `uses:` actions at all** (clones with `git`, runs system `python3`), so it works
  even under a locked-down "Allow owner actions only" policy — nothing to download or block.
- GitHub **auto-disables** idle scheduled workflows after **60 days**. If you get the
  "workflow disabled" email, click **Enable** to resume (or ask me to wire a Personal Access
  Token for a hands-off guarantee).
- **Silent-failure watchdog:** the agent tracks per-source health in `state/source_health.json`
  and sends a Telegram alert if a source that *used to work* goes dark (3 runs in a row) or if
  **all** sources fail — so "quietly broken" never looks like "no new roles yet."
- Once the cloud run works, **disable the local Windows task** to avoid double pings:
  `Disable-ScheduledTask -TaskName InternWatchAgent`  (re-enable with `Enable-ScheduledTask`).

---

## Commands

| Command | What it does |
|---|---|
| `python agent.py` | Normal run: fetch → diff → notify new → save state |
| `python agent.py --seed` | Mark all current matches as seen, **no** notifications (run once at setup) |
| `python agent.py --dry-run` | Preview new matches in the console; no notify, no state change |
| `python agent.py --all` | Print **all** current matches (ignores state) — good for sanity checks |
| `python agent.py --test-notify` | Send a test Telegram message |
| `python agent.py --year 2026` | Override target year (test against this year's live data) |
| `python agent.py --season summer` | Override target season |

---

## Tuning the target list

Edit `config.json`:

- **`filters.season` / `filters.year`** — what cycle to watch (`"summer"`, `"2027"`).
- **`greenhouse`** — board tokens. Find one from a company's careers URL
  `boards.greenhouse.io/<TOKEN>` or `job-boards.greenhouse.io/<TOKEN>`. Verify:
  `https://boards-api.greenhouse.io/v1/boards/<TOKEN>/jobs`.
- **`ashby`** — org slug from `jobs.ashbyhq.com/<SLUG>`. Verify:
  `https://api.ashbyhq.com/posting-api/job-board/<SLUG>`.
- **`lever`** — slug from `jobs.lever.co/<SLUG>`. Verify:
  `https://api.lever.co/v0/postings/<SLUG>?mode=json`.
- **`workday`** — direct, no-auth polling of a company's Workday board. Add
  `{ "name", "host", "tenant", "site" }`. Find `host/tenant/site` from the careers URL pattern
  `https://<host>/wday/cxs/<tenant>/<site>/jobs` (open the careers page, watch the network call).
  Shipped & verified: NVIDIA, Salesforce, Adobe, Intel.
- **`amazon`** — set `true` to poll Amazon's public `amazon.jobs` search (no auth).
- **`usajobs_keywords`** — federal jobs (NASA/NIST/agencies). **Off unless** you set a free
  `USAJOBS_API_KEY` env/secret (register at developer.usajobs.gov). Public-data key, not a login.
  Note: most DOE *contractor* labs (LLNL/Sandia/ORNL/Argonne/PNNL) post on their own ATSs, not
  USAJobs — add those as `workday`/`greenhouse` entries if they expose a public board.
- **`aggregators`** — raw `listings.json` URLs. Add the 2027 repos as they appear.

If a board returns 0 matches it usually just has no ML interns posted right now — not an error.

### What counts as a match
A posting is flagged when its **title/category** looks ML/AI (machine learning, AI/ML, NLP, CV,
LLM, applied/research scientist, data scientist, etc.) **and** it's an **internship/co-op**, the
**season/year** fits, and the **location is US** (or remote/unknown). Tune the keyword lists at
the top of `agent.py` (`_ML_PHRASES`, `_ML_TOKENS`, `_INTERN_RE`) if you want it broader/narrower.

---

## Files
```
intern_agent/
  agent.py             # the agent (single file, stdlib only)
  config.json          # your live config (holds secrets — keep private)
  config.example.json  # template
  register_task.ps1    # registers the Windows Scheduled Task
  state/seen.json      # auto-created: which roles you've already been told about
  logs/agent.log       # auto-created: run history
```

## Troubleshooting
- **No Telegram message:** re-run `--test-notify`. Make sure you messaged the bot first, and the
  `chat_id` is the numeric id (not the @username).
- **Everything reports as "new" and floods you:** you skipped `--seed`. Run it once.
- **Want to reset memory:** delete `state/seen.json` (then `--seed` again).
- **Corporate proxy blocks requests:** set `HTTPS_PROXY` env var before running.
