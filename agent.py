# -*- coding: utf-8 -*-
"""
Intern Watch Agent
==================
Monitors job sources for NEW ML/AI engineer *internship* roles (default: Summer 2027,
US) and sends a Telegram notification when fresh postings appear.

Sources (all public, no auth):
  1. Community internship aggregators (listings.json) -- broad coverage of 100s of
     companies. The "Summer 2027" editions go live mid-2026; until then they 404
     and are skipped gracefully.
  2. Direct ATS APIs (Greenhouse / Lever / Ashby) for specific target companies --
     catches roles the moment they post, often before aggregators pick them up.

Usage:
  python agent.py                 # normal run: fetch -> diff -> notify -> save state
  python agent.py --seed          # baseline: mark all current matches as seen, NO notify
  python agent.py --dry-run       # preview new matches in console, NO notify, NO save
  python agent.py --test-notify   # send a test Telegram message to verify config
  python agent.py --all           # print ALL current matches (ignores seen state)
  python agent.py --year 2026     # override target year (handy for testing now)
  python agent.py --season summer # override target season

Config: edit config.json next to this file. Secrets may instead be supplied via
env vars TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (these take precedence).
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

try:  # ensure unicode-safe console output on Windows (em-dashes etc.)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state", "seen.json")
LOG_PATH = os.path.join(HERE, "logs", "agent.log")
SSL_CTX = ssl.create_default_context()
UA = {"User-Agent": "intern-watch-agent/1.0 (+personal job monitor)"}

# --- US location matching -----------------------------------------------------
US_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY","DC",
}
US_NAMES = [
    "united states","usa","u.s.","u.s.a","america","alabama","alaska","arizona",
    "arkansas","california","colorado","connecticut","delaware","florida","georgia",
    "hawaii","idaho","illinois","indiana","iowa","kansas","kentucky","louisiana",
    "maine","maryland","massachusetts","michigan","minnesota","mississippi","missouri",
    "montana","nebraska","nevada","new hampshire","new jersey","new mexico","new york",
    "north carolina","north dakota","ohio","oklahoma","oregon","pennsylvania",
    "rhode island","south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming","seattle","bellevue",
    "redmond","kirkland","new york city","nyc","san francisco","bay area","mountain view",
    "menlo park","palo alto","sunnyvale","cupertino","los angeles","austin","boston",
    "chicago","pittsburgh","atlanta","denver","san diego","san jose","remote, us",
    "remote - us","remote (us",
]
# Explicit non-US signals (used to reject roles, incl. "Remote in <country>").
# Kept as multi-char tokens / comma-prefixed codes to avoid matching inside US city
# names (e.g. bare "uk" would hit "Milwaukee").
NON_US = [
    "canada","ontario","toronto","vancouver","montreal","quebec","british columbia",
    ", on,",", bc,",", qc,","united kingdom",", uk","u.k.","london","england","scotland",
    "ireland","dublin","india","bangalore","bengaluru","hyderabad","gurgaon","pune",
    "mumbai","new delhi","germany","berlin","munich","france","paris","netherlands",
    "amsterdam","spain","madrid","barcelona","singapore","australia","sydney","melbourne",
    "israel","tel aviv","japan","tokyo","china","beijing","shanghai","shenzhen",
    "hong kong","taiwan","korea","seoul","brazil","poland","warsaw","romania",
    "switzerland","zurich","sweden","stockholm","united arab emirates","dubai","mexico",
]

# --- logging ------------------------------------------------------------------
def log(msg):
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# --- http ---------------------------------------------------------------------
def fetch_json(url, timeout=35, retries=1):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.5)
    raise last

# --- normalization & filters --------------------------------------------------
_ML_PHRASES = [
    "machine learning", "deep learning", "artificial intelligence", "ml/ai", "ai/ml",
    "natural language", "computer vision", "generative", "research scientist",
    "applied scientist", "data scientist", "ml engineer", "ai engineer",
    "ml infrastructure", "ml platform", "foundation model", "large language",
    "reinforcement learning", "speech", "perception", "robotics",
]
_ML_TOKENS = re.compile(r"\b(ai|ml|mle|nlp|llm|llms|genai|cv|rl)\b", re.I)
_INTERN_RE = re.compile(r"\b(intern|interns|internship|co-?op)\b", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

def is_ml(title, category=""):
    t = (title or "").lower()
    c = (category or "").lower()
    if "machine learning" in c or "data science" in c or ("ai" in c and "machine" in c):
        return True
    if any(p in t for p in _ML_PHRASES):
        return True
    return bool(_ML_TOKENS.search(t))

def is_intern(title, commitment=""):
    blob = f"{title} {commitment}"
    return bool(_INTERN_RE.search(blob))

def is_us(loc):
    if not loc:
        return True  # unknown -> don't drop
    l = loc.lower()
    # 1) positive US signal wins
    if any(n in l for n in US_NAMES):
        return True
    for code in re.findall(r",\s*([A-Za-z]{2})\b", loc):
        if code.upper() in US_CODES:
            return True
    # 2) explicit non-US (incl. "Remote in <country>") -> reject
    if any(x in l for x in NON_US):
        return False
    # 3) bare "Remote" with no country -> assume US-eligible
    if "remote" in l:
        return True
    return False

def term_ok(term_text, season, year):
    """Aggregator term/season gate. season/year are the desired targets (lowercase)."""
    txt = (term_text or "").lower()
    if not txt:
        return True
    if season and season not in txt:
        return False
    years = _YEAR_RE.findall(txt)
    if years and year and year not in years:
        return False  # explicit non-target year
    return True

def year_not_stale(title, year):
    """ATS gate: drop only if title explicitly names a year earlier than target."""
    if not year:
        return True
    years = [int(y) for y in _YEAR_RE.findall(title or "")]
    if years and max(years) < int(year):
        return False
    return True

_SEASON_RE = re.compile(r"\b(summer|fall|autumn|winter|spring)\b", re.I)

def season_ok_title(title, season):
    """Drop roles whose title names a season OTHER than the target (e.g. 'Fall')."""
    found = {m.lower() for m in _SEASON_RE.findall(title or "")}
    return (not found) or (season in found)

EXCLUDE_TITLE = []  # lowercase keywords; set from config in main() (default ['phd'])

def title_allowed(title):
    t = (title or "").lower()
    return not any(k in t for k in EXCLUDE_TITLE)

def role_relevant(title, season):
    return season_ok_title(title, season) and title_allowed(title)

def norm_key(company, title):
    def n(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{n(company)}::{n(title)}"

# --- providers (each returns list of normalized job dicts) --------------------
def _job(company, title, url, location, term, source):
    return {"company": company.strip(), "title": (title or "").strip(),
            "url": url, "location": (location or "").strip(),
            "term": (term or "").strip(), "source": source}

def from_aggregator(url, season, year):
    out = []
    data = fetch_json(url)
    if not isinstance(data, list):
        return out
    for it in data:
        if not (it.get("active", True) and it.get("is_visible", True)):
            continue
        title = it.get("title", "")
        category = it.get("category", "")
        if not (is_ml(title, category) and is_intern(title)):
            continue
        if not role_relevant(title, season):
            continue
        terms = it.get("terms") or []
        term_text = " ".join(terms) + " " + (it.get("season") or "")
        if not term_ok(term_text, season, year):
            continue
        locs = it.get("locations") or []
        loc = "; ".join(locs) if isinstance(locs, list) else str(locs)
        if not is_us(loc):
            continue
        out.append(_job(it.get("company_name", "?"), title, it.get("url", ""),
                        loc, term_text.strip(), f"agg:{it.get('source','?')}"))
    return out

def from_greenhouse(token, season, year):
    out = []
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not (is_ml(title) and is_intern(title)):
            continue
        if not (role_relevant(title, season) and year_not_stale(title, year)):
            continue
        loc = (j.get("location") or {}).get("name", "")
        if not is_us(loc):
            continue
        out.append(_job(j.get("company_name") or token.title(), title,
                        j.get("absolute_url", ""), loc, "", f"greenhouse:{token}"))
    return out

def from_lever(slug, season, year):
    out = []
    data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        return out
    for j in data:
        title = j.get("text", "")
        cats = j.get("categories") or {}
        commitment = cats.get("commitment", "") or ""
        if not (is_ml(title) and is_intern(title, commitment)):
            continue
        if not (role_relevant(title, season) and year_not_stale(title, year)):
            continue
        loc = cats.get("location", "") or ""
        if not is_us(loc):
            continue
        out.append(_job(slug.replace("-", " ").title(), title,
                        j.get("hostedUrl", ""), loc, commitment, f"lever:{slug}"))
    return out

def from_ashby(org, season, year):
    out = []
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    for j in data.get("jobs", []):
        if not j.get("isListed", True):
            continue
        title = j.get("title", "")
        commitment = j.get("employmentType", "") or ""
        if not (is_ml(title) and is_intern(title, commitment)):
            continue
        if not (role_relevant(title, season) and year_not_stale(title, year)):
            continue
        loc = j.get("location", "") or ""
        if not is_us(loc):
            continue
        url = j.get("jobUrl") or j.get("applyUrl") or ""
        out.append(_job(org.replace("-", " ").title(), title, url, loc,
                        commitment, f"ashby:{org}"))
    return out

# --- state --------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)

# --- telegram -----------------------------------------------------------------
def telegram_send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def fmt_job(j):
    c = html.escape(j["company"]); t = html.escape(j["title"])
    loc = html.escape(j["location"] or "location n/a")
    term = f" · {html.escape(j['term'])}" if j["term"] else ""
    link = j["url"] or ""
    title_html = f'<a href="{html.escape(link, quote=True)}">{t}</a>' if link else t
    return f"• <b>{c}</b> — {title_html}\n   \U0001F4CD {loc}{term}"

def notify(token, chat_id, jobs):
    header = f"\U0001F916 <b>{len(jobs)} new ML/AI intern role(s)</b>"
    chunks, cur, size = [], [header], len(header)
    for j in jobs:
        block = fmt_job(j)
        if size + len(block) > 3500 and len(cur) > 1:
            chunks.append("\n\n".join(cur)); cur, size = [], 0
        cur.append(block); size += len(block) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    for ch in chunks:
        telegram_send(token, chat_id, ch)

# --- main ---------------------------------------------------------------------
def gather(cfg, season, year):
    tasks = []
    for u in cfg.get("aggregators", []):
        tasks.append(("agg", u, from_aggregator))
    for t in cfg.get("greenhouse", []):
        tasks.append(("gh", t, from_greenhouse))
    for s in cfg.get("lever", []):
        tasks.append(("lever", s, from_lever))
    for o in cfg.get("ashby", []):
        tasks.append(("ashby", o, from_ashby))

    jobs, ok, fail = [], 0, 0
    def run(task):
        kind, arg, fn = task
        return kind, arg, fn(arg, season, year)
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(run, t): t for t in tasks}
        for fut in cf.as_completed(futs):
            kind, arg, _ = futs[fut]
            try:
                _, _, res = fut.result()
                jobs.extend(res); ok += 1
                if res:
                    log(f"  + {kind}:{arg} -> {len(res)} match(es)")
            except Exception as e:
                fail += 1
                code = getattr(e, "code", type(e).__name__)
                log(f"  ! {kind}:{arg} unavailable ({code}) -- skipped")
    log(f"sources ok={ok} failed={fail} (failures are normal pre-launch / for 404s)")

    # de-dupe across sources by company+title
    seen_keys, uniq = set(), []
    for j in jobs:
        k = norm_key(j["company"], j["title"])
        if k in seen_keys:
            continue
        seen_keys.add(k); j["_key"] = k; uniq.append(j)
    return uniq

def load_config():
    if not os.path.exists(CONFIG_PATH):
        log(f"ERROR: missing {CONFIG_PATH} (copy config.example.json)"); sys.exit(2)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    tg = cfg.setdefault("telegram", {})
    # optional local secrets file (gitignored) so config.json stays commit-safe
    sp = os.path.join(HERE, "secrets.json")
    if os.path.exists(sp):
        try:
            with open(sp, "r", encoding="utf-8") as f:
                s = json.load(f)
            tg["bot_token"] = tg.get("bot_token") or s.get("bot_token", "")
            tg["chat_id"] = tg.get("chat_id") or s.get("chat_id", "")
        except Exception:
            pass
    # environment variables win (used by GitHub Actions / any CI)
    tg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", "")
    tg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id", "")
    return cfg

def main():
    ap = argparse.ArgumentParser(description="ML/AI internship watch agent")
    ap.add_argument("--seed", action="store_true", help="baseline current matches, no notify")
    ap.add_argument("--dry-run", action="store_true", help="preview, no notify, no save")
    ap.add_argument("--all", action="store_true", help="print all current matches")
    ap.add_argument("--test-notify", action="store_true", help="send a Telegram test message")
    ap.add_argument("--year", default=None, help="target year (default from config)")
    ap.add_argument("--season", default=None, help="target season (default from config)")
    args = ap.parse_args()

    cfg = load_config()
    flt = cfg.get("filters", {})
    season = (args.season or flt.get("season", "summer")).lower()
    year = (args.year or str(flt.get("year", "2027"))).strip()
    global EXCLUDE_TITLE
    EXCLUDE_TITLE = [k.lower() for k in flt.get("exclude_title_keywords", ["phd"])]
    token = cfg["telegram"]["bot_token"]; chat_id = cfg["telegram"]["chat_id"]

    if args.test_notify:
        if not (token and chat_id):
            log("ERROR: set telegram bot_token + chat_id (config.json or env vars).")
            sys.exit(2)
        telegram_send(token, chat_id,
                      "✅ <b>Intern Watch Agent connected.</b>\nYou'll get a ping here "
                      "when new ML/AI internships appear.")
        log("Test message sent."); return

    log(f"=== run start | target: {season} {year} | "
        f"{'DRY-RUN' if args.dry_run else 'SEED' if args.seed else 'LIVE'} ===")
    jobs = gather(cfg, season, year)
    log(f"total unique matches: {len(jobs)}")

    if args.all:
        for j in sorted(jobs, key=lambda x: x["company"].lower()):
            print(f"  {j['company']:<24} | {j['title']:<48} | {j['location']:<28} | {j['source']}")
        return

    state = load_state()
    new = [j for j in jobs if j["_key"] not in state]
    log(f"new since last run: {len(new)}")

    if args.dry_run:
        for j in sorted(new, key=lambda x: x["company"].lower()):
            print(f"  NEW  {j['company']:<22} | {j['title']:<46} | {j['location']:<26} | {j['url']}")
        log("dry-run: state NOT updated, no notifications sent."); return

    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    if args.seed:
        for j in jobs:
            state[j["_key"]] = {"company": j["company"], "title": j["title"],
                                "first_seen": now_iso, "seeded": True}
        save_state(state)
        log(f"seeded {len(jobs)} current matches as 'seen' (no notifications)."); return

    # LIVE
    if new:
        if token and chat_id:
            try:
                notify(token, chat_id, new)
                log(f"notified {len(new)} new role(s) via Telegram.")
            except Exception as e:
                log(f"ERROR sending Telegram: {e} -- printing instead.")
                for j in new:
                    print("  NEW", j["company"], "|", j["title"], "|", j["url"])
        else:
            log("No Telegram config -> printing new roles to console:")
            for j in new:
                print("  NEW", j["company"], "|", j["title"], "|", j["url"])
    for j in new:
        state[j["_key"]] = {"company": j["company"], "title": j["title"],
                            "url": j["url"], "first_seen": now_iso}
    save_state(state)
    log("=== run done ===")

if __name__ == "__main__":
    main()
