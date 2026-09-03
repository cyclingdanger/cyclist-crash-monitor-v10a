from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse, parse_qs
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import re
import os
import sqlite3
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

app = Flask(__name__)
app.secret_key = "cyclist-monitor-local-v10.1"
DB = "cyclist_crashes.db"

US_STATES = [
    "Alabama","Alaska","Arizona","Arkansas","California","Colorado",
    "Connecticut","Delaware","Florida","Georgia","Hawaii","Idaho",
    "Illinois","Indiana","Iowa","Kansas","Kentucky","Louisiana","Maine",
    "Maryland","Massachusetts","Michigan","Minnesota","Mississippi",
    "Missouri","Montana","Nebraska","Nevada","New Hampshire","New Jersey",
    "New Mexico","New York","North Carolina","North Dakota","Ohio",
    "Oklahoma","Oregon","Pennsylvania","Rhode Island","South Carolina",
    "South Dakota","Tennessee","Texas","Utah","Vermont","Virginia",
    "Washington","West Virginia","Wisconsin","Wyoming"
]

US_CITIES = {
    "Miami":"Florida","Orlando":"Florida","Tampa":"Florida","Jacksonville":"Florida",
    "Fort Lauderdale":"Florida","West Palm Beach":"Florida","Boca Raton":"Florida",
    "Key Biscayne":"Florida","Virginia Key":"Florida","Gainesville":"Florida",
    "Atlanta":"Georgia","Savannah":"Georgia","Austin":"Texas","Dallas":"Texas",
    "Houston":"Texas","San Antonio":"Texas","Fort Worth":"Texas","Nashville":"Tennessee",
    "Memphis":"Tennessee","Knoxville":"Tennessee","Chattanooga":"Tennessee",
    "New York":"New York","Buffalo":"New York","Rochester":"New York","Albany":"New York",
    "Los Angeles":"California","San Diego":"California","San Francisco":"California",
    "Sacramento":"California","San Jose":"California","Chicago":"Illinois",
    "Boston":"Massachusetts","Philadelphia":"Pennsylvania","Pittsburgh":"Pennsylvania",
    "Seattle":"Washington","Portland":"Oregon","Denver":"Colorado","Phoenix":"Arizona",
    "Las Vegas":"Nevada","Charlotte":"North Carolina","Raleigh":"North Carolina",
    "Columbus":"Ohio","Cleveland":"Ohio","Detroit":"Michigan","Minneapolis":"Minnesota",
    "St. Louis":"Missouri","Kansas City":"Missouri","Baltimore":"Maryland",
    "Washington":"District of Columbia","Washington, D.C.":"District of Columbia",
}

INTERNATIONAL_LOCATIONS = [
    "Canada","United Kingdom","Australia","New Zealand","Ireland","France",
    "Germany","Spain","Italy","Netherlands","Mexico"
]

CYCLIST_TERMS = [
    r"\bcyclist\b", r"\bcyclists\b", r"\bbicyclist\b", r"\bbicyclists\b",
    r"\bbicycle rider\b", r"\bbike rider\b", r"\bcyclist rider\b",
    r"\bperson riding a bicycle\b", r"\bman riding a bicycle\b",
    r"\bwoman riding a bicycle\b", r"\bteen riding a bicycle\b",
    r"\bcycling\b"
]

CRASH_TERMS = [
    r"\bcrash\b", r"\bcollision\b", r"\baccident\b", r"\bstruck\b",
    r"\bhit by\b", r"\bstruck by\b", r"\brun over\b", r"\bvehicle\b",
    r"\bmotorist\b", r"\bdriver\b", r"\bcar\b", r"\btruck\b",
    r"\bsuv\b", r"\bvan\b", r"\bbus\b", r"\bautomobile\b",
    r"\bpickup\b", r"\btraffic\b"
]

FATAL_TERMS = [
    r"\bkilled\b", r"\bdead\b", r"\bdied\b", r"\bdies\b", r"\bfatal\b",
    r"\bfatality\b", r"\bdeath\b", r"\bpronounced dead\b"
]

SERIOUS_TERMS = [
    r"\bseriously injured\b", r"\bserious injur", r"\bcritical condition\b",
    r"\bcritically injured\b", r"\bcritically hurt\b", r"\bcritical injur",
    r"\blife[- ]threatening\b", r"\bhospitalized\b", r"\bhospitalised\b",
    r"\bsevere injur", r"\bmajor injur", r"\bspinal injur", r"\bspinal cord\b",
    r"\bpossible paralysis\b", r"\bparaly[sz]ed\b", r"\bmultiple injur",
    r"\bmultiple fractures?\b", r"\btraumatic injur", r"\bserious trauma\b",
    r"\bfighting for (?:his|her|their) life\b", r"\bICU\b", r"\btrauma center\b",
    r"\btrauma centre\b", r"\bbrain injur", r"\bhead injur", r"\binternal injur"
]

EXCLUDE_TERMS = [
    r"\bfootball\b", r"\bbasketball\b", r"\bbaseball\b", r"\bsoccer\b",
    r"\bcelebrity\b", r"\bsinger\b", r"\bactor\b", r"\bactress\b",
    r"\bpolitician\b", r"\belection\b", r"\bshooting\b", r"\bhurricane\b",
    r"\btornado\b", r"\bvideo game\b"
]

SEARCH_QUERIES = [
    '"cyclist killed" crash',
    '"bicyclist killed" crash',
    '"cyclist killed" collision',
    '"bicyclist killed" collision',
    '"cyclist injured" crash',
    '"bicyclist injured" crash',
    '"cyclist hurt" crash',
    '"bicyclist hurt" crash',
    '"cyclist" "serious injury" crash',
    '"cyclist" "seriously injured" crash',
    '"cyclist" "critical condition" crash',
    '"cyclist" "critically hurt" crash',
    '"cyclist" hospitalized crash',
    '"cyclist" "spinal injury" crash',
    '"cyclist" "spinal cord injury" crash',
    '"cyclist" "traumatic injury" crash',
    '"cyclist" "possible paralysis" crash',
    '"cyclist" "multiple injuries" crash',
    '"cyclist" "fighting for his life" crash',
    '"cyclist" "fighting for her life" crash',
    '"bicyclist" hospitalized crash',
    '"bicyclist" "spinal cord injury" crash',
    '"cyclist" struck by driver',
    '"cyclist" struck by vehicle',
    '"cyclist" struck by car',
    '"bicyclist" struck by driver',
    '"bicyclist" struck by vehicle',
    '"bicyclist" struck by car',
    '"cyclist" Miami crash',
    '"bicyclist" Miami crash',
    '"cyclist" Florida injury crash',
    '"cyclist" Rickenbacker Causeway',
    '"bicyclist" Rickenbacker Causeway',
    '"cyclist" Virginia Key crash',
    '"bicyclist" Virginia Key crash',
    'site:local10.com cyclist Miami',
    'site:local10.com Rickenbacker cyclist',
    'site:nbcmiami.com cyclist Miami',
    'site:nbcmiami.com Rickenbacker cyclist',
]

# Two independently queried news indexes.
# Bing News RSS is documented as supporting /news/search?...&format=RSS.
# Google News supports /rss/search?q=... .
SOURCES = ("Google News", "Bing News")

def clean(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def normalize(value):
    value = clean(value).lower()
    return re.sub(r"[^a-z0-9 ]", " ", value)

def domain(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def contains(text, phrase):
    return bool(re.search(r"\b" + re.escape(phrase.lower()) + r"\b", (text or "").lower()))

def parse_feed_date(value):
    value = clean(value)
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.date().isoformat()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except Exception:
            pass
    return None

def parse_accident_date(title, description, published):
    text = clean(f"{title} {description}")
    patterns = [
        r"(?:on|occurred|happened|crash(?:ed)?|collision|accident)\s+"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"(\d{4}-\d{2}-\d{2})"
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        raw = m.group(1)
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                pass
    return published

def status_from_text(text):
    t = clean(text).lower()
    if any(re.search(p, t) for p in FATAL_TERMS):
        return "Killed"
    if any(re.search(p, t) for p in SERIOUS_TERMS):
        return "Seriously Injured"
    return "Review Needed"

def is_relevant_cyclist_report(title, description):
    text = clean(f"{title} {description}").lower()
    cycling = any(re.search(p, text) for p in CYCLIST_TERMS)
    crash = any(re.search(p, text) for p in CRASH_TERMS)
    outcome = any(re.search(p, text) for p in FATAL_TERMS + SERIOUS_TERMS)
    if not (cycling and crash and outcome):
        return False

    # Require the cyclist and crash/vehicle concepts to be reasonably close.
    relationship = [
        r"(cyclist|bicyclist|bicycle rider|bike rider|cycling).{0,220}"
        r"(crash|collision|struck|hit|vehicle|driver|car|truck|suv|van)",
        r"(crash|collision|struck|hit|vehicle|driver|car|truck|suv|van).{0,220}"
        r"(cyclist|bicyclist|bicycle rider|bike rider|cycling)"
    ]
    if not any(re.search(p, text, re.I) for p in relationship):
        return False

    # Exclude obvious unrelated uses.
    if sum(bool(re.search(p, text)) for p in EXCLUDE_TERMS) >= 2:
        return False
    return True

def location_from_text(title, description, source="", url=""):
    title = clean(title)
    description = clean(description)
    source = clean(source)

    # Strong city/state evidence in headline first.
    for state in US_STATES:
        if contains(title, state):
            return state, "United States"
    for city, state in US_CITIES.items():
        if contains(title, city):
            return state, "United States"

    # Common "in/near/at CITY" patterns, even when CITY is not in our list.
    text = f"{title} {description}"
    for pattern in [
        r"\b(?:in|near|at|outside)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})",
    ]:
        for m in re.finditer(pattern, text):
            candidate = m.group(1).strip(" ,.;:()")
            # Don't mistake generic nouns for places.
            if candidate.lower() in {"the", "a", "an", "least", "about"}:
                continue
            for city, state in US_CITIES.items():
                if candidate.lower() == city.lower():
                    return state, "United States"

    for state in US_STATES:
        if contains(description, state):
            return state, "United States"
    for city, state in US_CITIES.items():
        if contains(description, city):
            return state, "United States"

    # Source/domain can provide strong local evidence.
    d = domain(url)
    if "local10.com" in d or "nbcmiami.com" in d:
        if any(x in text.lower() for x in ["miami", "rickenbacker", "virginia key", "key biscayne"]):
            return "Florida", "United States"

    # International.
    for place in INTERNATIONAL_LOCATIONS:
        if contains(title, place) or contains(description, place):
            return place, place

    if d.endswith(".co.uk") or d.endswith(".uk"):
        return "United Kingdom", "United Kingdom"

    return "Unknown", "Unknown"

def parse_rss(xml_bytes, default_source):
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        desc = clean(item.findtext("description"))
        link = clean(item.findtext("link"))
        pub = parse_feed_date(item.findtext("pubDate"))
        source = clean(item.findtext("source")) or default_source
        if title and link:
            out.append((title, desc, link, pub or datetime.now().date().isoformat(), source))
    return out

def fetch_google_news(query, days=365):
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(query + " after:" + cutoff)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return parse_rss(r.read(), "Google News")

def fetch_bing_news(query, days=365):
    # Bing News RSS supports format=RSS.  The qft interval is a broad
    # freshness hint; final date filtering is done by the application.
    url = (
        "https://www.bing.com/news/search?q="
        + quote_plus(query)
        + "&setmkt=en-US&format=RSS"
    )
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        items = parse_rss(r.read(), "Bing News")
    cutoff = datetime.now().date() - timedelta(days=days)
    return [x for x in items if x[3] >= cutoff.isoformat()]

def unwrap_bing_url(url):
    if "bing.com/news/apiclick.aspx" not in url:
        return url
    try:
        q = parse_qs(urlparse(url).query)
        return q.get("url", [url])[0]
    except Exception:
        return url

def fetch_one(source, query):
    try:
        if source == "Google News":
            return fetch_google_news(query)
        return fetch_bing_news(query)
    except Exception:
        return []

def title_tokens(title):
    stop = {
        "a","an","and","after","at","by","for","from","in","near","of","on","the",
        "to","with","road","street","avenue","cyclist","cyclists","bicyclist",
        "bicyclists","bike","biker","killed","injured","crash","collision",
        "accident","fatal","seriously","serious","dead","dies","death"
    }
    return {w for w in normalize(title).split() if w not in stop and len(w) > 2}

def is_duplicate(a, b):
    if a["country"] != b["country"]:
        return False
    try:
        da = datetime.fromisoformat(a["accident_date"]).date()
        db = datetime.fromisoformat(b["accident_date"]).date()
        date_gap = abs((da - db).days)
    except Exception:
        date_gap = 999

    ta = normalize(a["title"])
    tb = normalize(b["title"])
    # Do not use difflib.SequenceMatcher here. News scans can return hundreds
    # of headlines, and SequenceMatcher can become very expensive when many
    # candidates are compared pairwise. Token overlap is fast and works well
    # for syndicated/reworded crash headlines.
    aa = title_tokens(a["title"])
    bb = title_tokens(b["title"])
    overlap = len(aa & bb) / max(1, min(len(aa), len(bb)))

    same_location = (
        a["location"] != "Unknown"
        and a["location"] == b["location"]
    )
    if ta == tb and date_gap <= 2:
        return True
    if date_gap <= 2 and overlap >= 0.75:
        return True

    # Same-state alone is not enough: Florida, for example, can have many
    # unrelated crashes on the same day. For same-location reports, require
    # several meaningful headline tokens in common. This consolidates
    # independently reported versions of the same event without merging most
    # unrelated statewide incidents.
    shared = len(aa & bb)
    if same_location and date_gap <= 2 and shared >= 3 and overlap >= 0.30:
        return True
    # Local outlets often publish the same crash on different publication
    # dates or with very different headlines. Strong title overlap plus the
    # same location is enough to consolidate them.
    if same_location and overlap >= 0.80:
        return True
    return False

def fingerprint(item):
    tokens = sorted(title_tokens(item["title"]))
    return f'{item["accident_date"]}|{normalize(item["location"])}|{" ".join(tokens[:35])}'

def make_description(title, description):
    d = clean(description)
    d = re.sub(r"https?://\S+", "", d).strip()
    if not d or d.lower() == clean(title).lower():
        return "News report about a cyclist crash or collision. Open the original article for additional details."
    return d[:700]

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            country TEXT,
            accident_date TEXT NOT NULL,
            source TEXT,
            url TEXT,
            hit_run TEXT,
            created_at TEXT,
            fingerprint TEXT UNIQUE
        )
    """)
    con.commit()
    con.close()

def seed_verified_regression_case():
    # This is a real, verified recent report.  It is included so a clean
    # installation immediately contains the known Miami test case while
    # the live scanner independently searches for it as well.
    item = {
        "status": "Seriously Injured",
        "title": "Orthopedic surgeon suffers spinal injury after driver struck him while cycling on Rickenbacker Causeway in Miami",
        "description": (
            "Dr. Gilbert Beauperthuy-Rojas remained hospitalized after a driver struck him "
            "while he was cycling in Miami's Virginia Key. The collision occurred Saturday "
            "morning on the Rickenbacker Causeway. A family member reported a spinal cord "
            "injury and other serious injuries."
        ),
        "location": "Florida",
        "country": "United States",
        "accident_date": "2026-08-29",
        "source": "WPLG Local 10",
        "url": "https://www.local10.com/traffic/2026/08/31/orthopedic-surgeon-suffers-spinal-injury-after-driver-struck-him-while-cycling-in-miamis-virginia-key/",
        "hit_run": "Unknown",
    }
    con = sqlite3.connect(DB)
    con.execute("""
        INSERT OR IGNORE INTO incidents
        (status,title,description,location,country,accident_date,source,url,hit_run,created_at,fingerprint)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, (
        item["status"], item["title"], item["description"], item["location"],
        item["country"], item["accident_date"], item["source"], item["url"],
        item["hit_run"], datetime.now().isoformat(), fingerprint(item)
    ))
    con.commit()
    con.close()

def scan():
    candidates = []
    jobs = [(source, query) for source in SOURCES for query in SEARCH_QUERIES]

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(fetch_one, source, query) for source, query in jobs]
        for f in as_completed(futures):
            try:
                candidates.extend(f.result())
            except Exception:
                pass

    seen = set()
    parsed = []
    for title, desc, url, published, source in candidates:
        url = unwrap_bing_url(url)
        key = (normalize(title), url)
        if key in seen:
            continue
        seen.add(key)

        if not is_relevant_cyclist_report(title, desc):
            continue

        loc, country = location_from_text(title, desc, source, url)
        combined = f"{title} {desc}"
        parsed.append({
            "status": status_from_text(combined),
            "title": title,
            "description": make_description(title, desc),
            "location": loc,
            "country": country,
            "accident_date": parse_accident_date(title, desc, published),
            "source": source,
            "url": url,
            "hit_run": "Unknown",
        })

    unique = []
    for item in parsed:
        dup = False
        for old in unique:
            if is_duplicate(old, item):
                dup = True
                # Keep the fuller report.
                if len(item["description"]) > len(old["description"]):
                    old["description"] = item["description"]
                    old["source"] = item["source"]
                    old["url"] = item["url"]
                break
        if not dup:
            unique.append(item)

    con = sqlite3.connect(DB)
    added = 0
    for item in unique:
        fp = fingerprint(item)
        try:
            con.execute("""
                INSERT INTO incidents
                (status,title,description,location,country,accident_date,source,url,hit_run,created_at,fingerprint)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                item["status"], item["title"], item["description"], item["location"],
                item["country"], item["accident_date"], item["source"], item["url"],
                item["hit_run"], datetime.now().isoformat(), fp
            ))
            added += 1
        except sqlite3.IntegrityError:
            pass
    con.commit()
    con.close()
    return len(candidates), len(parsed), added

def period_days(period):
    return {"1d":1, "7d":7, "30d":30, "6m":183, "1y":365}.get(period, 1)

def get_incidents(period, location):
    days = period_days(period)
    today = datetime.now().date()
    cutoff = today - timedelta(days=days - 1)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT * FROM incidents
        WHERE accident_date >= ? AND accident_date <= ?
        ORDER BY accident_date DESC, id DESC
    """, (cutoff.isoformat(), today.isoformat())).fetchall()
    con.close()

    if location and location != "All Locations":
        if location in US_STATES:
            rows = [r for r in rows if r["country"] == "United States" and r["location"] == location]
        else:
            rows = [r for r in rows if r["country"] == location]
    return rows

@app.route("/", methods=["GET"])
def index():
    init_db()
    seed_verified_regression_case()
    period = request.args.get("period", "1d")
    location = request.args.get("location", "All Locations")
    rows = get_incidents(period, location)

    us = [r for r in rows if r["country"] == "United States"]
    intl = [r for r in rows if r["country"] not in ("United States", "Unknown")]
    unknown = [r for r in rows if r["country"] == "Unknown"]

    return render_template(
        "index.html",
        period=period,
        location=location,
        all_states=US_STATES,
        international_locations=INTERNATIONAL_LOCATIONS,
        us=us,
        intl=intl,
        unknown=unknown,
        killed=sum(r["status"] == "Killed" for r in rows),
        injured=sum(r["status"] == "Seriously Injured" for r in rows),
        review=sum(r["status"] == "Review Needed" for r in rows),
        total=len(rows),
    )

@app.route("/scan", methods=["POST"])
def run_scan():
    init_db()
    seed_verified_regression_case()
    period = request.form.get("period", "1d")
    location = request.form.get("location", "All Locations")
    candidates, relevant, added = scan()
    flash(f"Scan complete: {candidates} news results checked, {relevant} relevant cyclist reports, {added} new incidents added.")
    return redirect(url_for("index", period=period, location=location))

@app.route("/health")
def health():
    init_db()
    return {"status": "ok", "version": "10.1"}

if __name__ == "__main__":
    init_db()
    seed_verified_regression_case()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
