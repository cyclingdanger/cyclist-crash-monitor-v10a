from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import re
import html
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
app.secret_key = "cyclist-monitor-local"

DB = str(Path(__file__).resolve().parent / "incidents.db")


# ============================================================
# U.S. LOCATIONS
# ============================================================

US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "District of Columbia": "DC",
}

US_CITIES = {
    "Jacksonville": "Florida", "Miami": "Florida", "Orlando": "Florida",
    "Tampa": "Florida", "Fort Lauderdale": "Florida",
    "West Palm Beach": "Florida", "Palm Beach": "Florida",
    "Boston": "Massachusetts", "Springfield": "Massachusetts",
    "Worcester": "Massachusetts", "New York City": "New York",
    "Buffalo": "New York", "Rochester": "New York",
    "Charlotte": "North Carolina", "Raleigh": "North Carolina",
    "Wilmington": "North Carolina", "Los Angeles": "California",
    "San Diego": "California", "San Francisco": "California",
    "Sacramento": "California", "Chicago": "Illinois",
    "Philadelphia": "Pennsylvania", "Pittsburgh": "Pennsylvania",
    "Denver": "Colorado", "Phoenix": "Arizona", "Seattle": "Washington",
    "Portland": "Oregon", "Dallas": "Texas", "Houston": "Texas",
    "Austin": "Texas", "Atlanta": "Georgia", "Nashville": "Tennessee",
    "Memphis": "Tennessee", "Knoxville": "Tennessee",
    "Chattanooga": "Tennessee", "Murfreesboro": "Tennessee",
    "Clarksville": "Tennessee", "Columbus": "Ohio",
    "Cleveland": "Ohio", "Detroit": "Michigan",
    "Minneapolis": "Minnesota", "St. Louis": "Missouri",
    "Kansas City": "Missouri", "Las Vegas": "Nevada",
    "New Orleans": "Louisiana", "Baton Rouge": "Louisiana",
    "Chattanooga": "Tennessee", "Johnson City": "Tennessee",
    "Kingsport": "Tennessee", "Franklin": "Tennessee",
    "Macon": "Georgia", "Savannah": "Georgia", "Austin": "Texas",
}


# ============================================================
# INTERNATIONAL LOCATIONS
# ============================================================

INTL_COUNTRIES = {
    "United Kingdom", "UK", "England", "Scotland", "Wales",
    "Northern Ireland", "Ireland", "Republic of Ireland", "Canada",
    "Australia", "New Zealand", "France", "Spain", "Germany", "Italy",
    "Netherlands", "Belgium", "Denmark", "Sweden", "Norway", "Finland",
    "Portugal", "Mexico", "Brazil", "South Africa", "India", "Japan",
    "China",
}

INTL_CITIES = {
    "Dublin": "Ireland", "Cork": "Ireland", "Galway": "Ireland",
    "Meath": "Ireland", "London": "United Kingdom",
    "Manchester": "United Kingdom", "Birmingham": "United Kingdom",
    "Liverpool": "United Kingdom", "Bristol": "United Kingdom",
    "Leeds": "United Kingdom", "Glasgow": "United Kingdom",
    "Edinburgh": "United Kingdom", "Cardiff": "United Kingdom",
    "Belfast": "United Kingdom", "Northampton": "United Kingdom",
    "Wiltshire": "United Kingdom", "Yorkshire": "United Kingdom",
    "Kent": "United Kingdom", "Essex": "United Kingdom",
    "Surrey": "United Kingdom", "Hampshire": "United Kingdom",
    "Devon": "United Kingdom", "Southampton": "United Kingdom",
    "Toronto": "Canada", "Vancouver": "Canada", "Montreal": "Canada",
    "Calgary": "Canada", "Paris": "France", "Madrid": "Spain",
    "Barcelona": "Spain", "Berlin": "Germany", "Rome": "Italy",
    "Amsterdam": "Netherlands", "Auckland": "New Zealand",
    "Sydney": "Australia", "Melbourne": "Australia",
}


# ============================================================
# HELPERS
# ============================================================

def clean(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", value)


def contains(text, phrase):
    return bool(re.search(r"\b" + re.escape(phrase.lower()) + r"\b",
                          text.lower()))


def domain_from_url(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


# ============================================================
# LOCATION DETECTION
# ============================================================

def location_from_text(title, description, source="", url=""):
    """
    Returns:
        location, country, kind

    U.S. incidents:
        location = state name
        country = United States
        kind = us

    International incidents:
        location = country name
        country = country name
        kind = international

    Unknown:
        Unknown, Unknown, unknown

    IMPORTANT:
    International city/country evidence is checked before U.S. evidence.
    This prevents a word such as Massachusetts appearing in an unrelated
    U.K. story from automatically making the story a Massachusetts story.
    """

    title = clean(title)
    description = clean(description)
    source = clean(source)

    title_lower = title.lower()
    body_lower = description.lower()
    source_lower = source.lower()
    domain = domain_from_url(url)

    # --------------------------------------------------------
    # 1. Very strong international signals in the headline
    # --------------------------------------------------------

    intl_scores = {}

    for city, country in INTL_CITIES.items():
        if contains(title, city):
            intl_scores[country] = intl_scores.get(country, 0) + 50

    for country_name in INTL_COUNTRIES:
        if contains(title, country_name):
            intl_scores[country_name] = intl_scores.get(country_name, 0) + 50

    # UK source/domain is strong evidence.
    uk_domains = (
        ".co.uk", ".uk", "bbc.", "independent.co.uk",
        "theguardian.com", "yorkshirepost.co.uk",
        "northamptonchron.co.uk"
    )
    if domain.endswith(".co.uk") or domain.endswith(".uk") or any(
        token in domain for token in uk_domains if "." in token
    ):
        intl_scores["United Kingdom"] = intl_scores.get(
            "United Kingdom", 0
        ) + 40

    # --------------------------------------------------------
    # 2. Strong U.S. headline evidence
    # --------------------------------------------------------

    state_scores = {state: 0 for state in US_STATES}

    for state in US_STATES:
        if contains(title, state):
            state_scores[state] += 40

    for city, state in US_CITIES.items():
        if contains(title, city):
            state_scores[state] += 45

    # International wins if it has stronger evidence.
    best_intl = max(intl_scores, key=intl_scores.get) if intl_scores else None
    best_intl_score = intl_scores.get(best_intl, 0) if best_intl else 0

    best_state = max(state_scores, key=state_scores.get)
    best_state_score = state_scores[best_state]

    if best_intl_score > 0 and best_intl_score >= best_state_score:
        return best_intl, best_intl, "international"

    if best_state_score > 0:
        return best_state, "United States", "us"

    # --------------------------------------------------------
    # 3. Description/body evidence
    # --------------------------------------------------------

    intl_scores = {}

    for city, country in INTL_CITIES.items():
        if contains(description, city):
            intl_scores[country] = intl_scores.get(country, 0) + 15

    for country_name in INTL_COUNTRIES:
        if contains(description, country_name):
            intl_scores[country_name] = intl_scores.get(country_name, 0) + 15

    if domain.endswith(".co.uk") or domain.endswith(".uk"):
        intl_scores["United Kingdom"] = intl_scores.get(
            "United Kingdom", 0
        ) + 30

    best_intl = max(intl_scores, key=intl_scores.get) if intl_scores else None
    if best_intl:
        return best_intl, best_intl, "international"

    for state in US_STATES:
        if contains(description, state):
            return state, "United States", "us"

    for city, state in US_CITIES.items():
        if contains(description, city):
            return state, "United States", "us"

    # --------------------------------------------------------
    # 4. Source name can help when title/body are sparse
    # --------------------------------------------------------

    for city, country in INTL_CITIES.items():
        if contains(source, city):
            return country, country, "international"

    for state in US_STATES:
        if contains(source, state):
            return state, "United States", "us"

    return "Unknown", "Unknown", "unknown"


# ============================================================
# STATUS
# ============================================================

def status_from_text(text):
    text = clean(text).lower()

    killed_words = [
        "killed", "dead", "died", "dies", "fatal", "death",
        "struck and killed", "cyclist dies", "bicyclist dies",
        "fatal crash", "fatal collision", "pronounced dead",
    ]

    injured_words = [
        "seriously injured", "serious injuries", "critical condition",
        "life-threatening", "critically injured", "hospitalized",
        "severe injuries", "major injuries", "serious injury",
        "life threatening",
    ]

    if any(word in text for word in killed_words):
        return "Killed"

    if any(word in text for word in injured_words):
        return "Seriously Injured"

    return "Review Needed"


# ============================================================
# DATE PARSING
# ============================================================

def parse_rss_date(value):
    value = clean(value)
    formats = (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%d",
    )

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            continue

    return None


def parse_date(text, fallback=None):
    text = clean(text)

    patterns = [
        r"(?:on|occurred|happened|crash|collision|accident)\s+"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        raw = match.group(1)

        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                pass

    return fallback


# ============================================================
# DESCRIPTION
# ============================================================

def make_description(title, description):
    """
    Google News often gives us an RSS description containing HTML,
    the headline again, and the publisher. Never use the URL itself
    as the displayed description.
    """

    title = clean(title)
    description = clean(description)

    # Remove a leading publisher/source prefix.
    description = re.sub(
        r"^\s*(?:[A-Za-z0-9&.'’ -]{1,100})\s*:\s*",
        "",
        description
    ).strip()

    # Remove an exact repeated headline.
    if description.lower().startswith(title.lower()):
        description = description[len(title):].strip(" -:|")

    # Remove obvious URL-only text.
    description = re.sub(r"https?://\S+", "", description).strip()

    if not description or description.lower() == title.lower():
        return (
            "News report about a cyclist crash or collision. "
            "Open the original article for additional details."
        )

    return description[:700]


# ============================================================
# DUPLICATE DETECTION
# ============================================================

STOP_WORDS = {
    "a", "an", "and", "after", "at", "by", "for", "from", "in", "near",
    "of", "on", "the", "to", "with", "road", "street", "avenue",
    "cyclist", "cyclists", "bicyclist", "bicyclists", "bike", "biker",
    "killed", "injured", "crash", "collision", "accident", "fatal",
    "seriously", "serious", "dead", "dies", "death",
}


def title_tokens(title):
    return {
        word for word in normalize(title).split()
        if word not in STOP_WORDS and len(word) > 2
    }


def fingerprint(title, location, accident_date):
    tokens = sorted(title_tokens(title))
    core = " ".join(tokens[:35])

    return f"{accident_date}|{normalize(location)}|{core}"


def is_duplicate(existing, new):
    if existing["accident_date"] != new["accident_date"]:
        return False

    if existing["country"] != new["country"]:
        return False

    # Unknown location is deliberately not enough to merge stories.
    # We require strong title similarity when location is unknown.
    title_a = normalize(existing["title"])
    title_b = normalize(new["title"])

    tokens_a = title_tokens(existing["title"])
    tokens_b = title_tokens(new["title"])

    shared = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    similarity = shared / max(1, union)
    overlap = shared / max(
        1, min(len(tokens_a), len(tokens_b))
    )

    same_location = (
        normalize(existing["location"])
        == normalize(new["location"])
        and existing["location"] != "Unknown"
    )

    # Same event with nearly identical wording.
    if similarity >= 0.74:
        return True

    # Different publishers often rewrite the same event substantially.
    if same_location and similarity >= 0.52 and overlap >= 0.55:
        return True

    # Strong token overlap even if one headline adds/removes words.
    if same_location and overlap >= 0.72:
        return True

    return False


# ============================================================
# DATABASE
# ============================================================

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY,
            status TEXT,
            title TEXT,
            description TEXT,
            location TEXT,
            country TEXT,
            accident_date TEXT,
            source TEXT,
            url TEXT,
            hit_run TEXT,
            created_at TEXT,
            fingerprint TEXT UNIQUE
        )
    """)
    con.commit()
    con.close()
    migrate_and_dedupe_db()


def migrate_and_dedupe_db():
    """
    Repair old records once at startup and remove duplicate incidents.
    This is NOT called on every page request, which keeps filtering fast.
    """

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT * FROM incidents ORDER BY id"
    ).fetchall()

    # Reclassify all existing records.
    for row in rows:
        location, country, _ = location_from_text(
            row["title"],
            row["description"],
            row["source"],
            row["url"],
        )

        description = make_description(
            row["title"],
            row["description"],
        )

        con.execute(
            """
            UPDATE incidents
            SET location = ?, country = ?, description = ?
            WHERE id = ?
            """,
            (location, country, description, row["id"]),
        )

    con.commit()

    rows = con.execute(
        "SELECT * FROM incidents ORDER BY id"
    ).fetchall()

    kept = []
    delete_ids = set()

    for row in rows:
        current = dict(row)

        duplicate_row = None

        for existing in kept:
            if is_duplicate(existing, current):
                duplicate_row = existing
                break

        if duplicate_row is None:
            kept.append(current)
            continue

        # Keep the record with the fuller description.
        if len(current["description"] or "") > len(
            duplicate_row["description"] or ""
        ):
            con.execute(
                """
                UPDATE incidents
                SET description = ?, source = ?, url = ?
                WHERE id = ?
                """,
                (
                    current["description"],
                    current["source"],
                    current["url"],
                    duplicate_row["id"],
                ),
            )

        delete_ids.add(current["id"])

    for incident_id in delete_ids:
        con.execute(
            "DELETE FROM incidents WHERE id = ?",
            (incident_id,),
        )

    con.commit()
    con.close()


# ============================================================
# GOOGLE NEWS
# ============================================================

def fetch_google_news(query, days=365):
    cutoff = (
        datetime.now() - timedelta(days=days)
    ).strftime("%Y-%m-%d")

    search_url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(query + " after:" + cutoff)
        + "&hl=en-US&gl=US&ceid=US:en"
    )

    req = urllib.request.Request(
        search_url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    with urllib.request.urlopen(req, timeout=5) as response:
        data = response.read()

    root = ET.fromstring(data)
    results = []

    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        description = clean(item.findtext("description"))
        link = clean(item.findtext("link"))
        pub_date = clean(item.findtext("pubDate"))
        source = clean(item.findtext("source")) or "Google News"

        published = parse_rss_date(pub_date)

        if not published:
            published = datetime.now().date().isoformat()

        results.append(
            (
                title,
                description,
                link,
                published,
                source,
            )
        )

    return results


# ============================================================
# SCAN
# ============================================================

def scan():
    # Keep the search list focused. Broad searches are supplemented
    # by state-specific searches for the states most likely to produce
    # local reports.
    queries = [
        '"cyclist killed" crash',
        '"cyclist killed" collision',
        '"bicyclist killed" crash',
        '"bicyclist killed" collision',
        '"cyclist seriously injured" crash',
        '"cyclist seriously injured" collision',
        '"bicyclist seriously injured" crash',
        '"cyclist" "critical condition" crash',
        '"cyclist" "serious injuries" crash',
        '"bicyclist" "fatal crash"',
        '"bicyclist" "fatal collision"',
        '"cyclist killed" Tennessee',
        '"bicyclist killed" Tennessee',
        '"cyclist seriously injured" Tennessee',
        '"cyclist killed" Florida',
        '"cyclist killed" Texas',
        '"cyclist killed" California',
        '"cyclist killed" New York',
        '"cyclist killed" United Kingdom',
        '"cyclist killed" UK',
        '"cyclist killed" Canada',
    ]

    candidates = []

    # Parallel requests make scanning much less likely to appear frozen.
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(fetch_google_news, query, 365): query
            for query in queries
        }

        for future in as_completed(futures):
            try:
                candidates.extend(future.result())
            except Exception:
                continue

    # Exact RSS duplicates first.
    seen = set()
    parsed = []

    for (
        title,
        description,
        url,
        publication_date,
        source,
    ) in candidates:

        exact_key = (
            normalize(title),
            normalize(url),
        )

        if exact_key in seen:
            continue

        seen.add(exact_key)

        combined = f"{title} {description}"

        location, country, _ = location_from_text(
            title,
            description,
            source,
            url,
        )

        status = status_from_text(combined)

        # Prefer an actual date found in the article text.
        accident_date = parse_date(
            combined,
            publication_date,
        )

        parsed.append({
            "status": status,
            "title": title,
            "description": make_description(
                title,
                description,
            ),
            "location": location,
            "country": country,
            "accident_date": accident_date,
            "source": source,
            "url": url,
            "hit_run": "Unknown",
        })

    # Cross-publisher duplicate removal.
    unique = []

    for incident in parsed:
        duplicate_index = None

        for index, existing in enumerate(unique):
            if is_duplicate(existing, incident):
                duplicate_index = index
                break

        if duplicate_index is None:
            unique.append(incident)
        else:
            existing = unique[duplicate_index]

            if len(incident["description"]) > len(
                existing["description"]
            ):
                existing["description"] = incident["description"]
                existing["source"] = incident["source"]
                existing["url"] = incident["url"]

    con = sqlite3.connect(DB)
    added = 0

    for incident in unique:
        fp = fingerprint(
            incident["title"],
            incident["location"],
            incident["accident_date"],
        )

        try:
            con.execute(
                """
                INSERT INTO incidents (
                    status, title, description, location, country,
                    accident_date, source, url, hit_run,
                    created_at, fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident["status"],
                    incident["title"],
                    incident["description"],
                    incident["location"],
                    incident["country"],
                    incident["accident_date"],
                    incident["source"],
                    incident["url"],
                    incident["hit_run"],
                    datetime.now().isoformat(),
                    fp,
                ),
            )
            added += 1
        except sqlite3.IntegrityError:
            # Existing fingerprint = already stored.
            pass

    con.commit()
    con.close()

    # Clean up duplicates created by older versions after the scan.
    migrate_and_dedupe_db()

    return len(candidates), added


# ============================================================
# GET INCIDENTS
# ============================================================

def get_incidents(period, location):
    period_days = {
        "1d": 1,
        "7d": 7,
        "30d": 30,
        "6m": 183,
        "1y": 365,
    }

    days = period_days.get(period, 1)
    today = datetime.now().date()
    cutoff = today - timedelta(days=days - 1)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT *
        FROM incidents
        WHERE accident_date >= ?
          AND accident_date <= ?
        ORDER BY accident_date DESC, id DESC
        """,
        (
            cutoff.isoformat(),
            today.isoformat(),
        ),
    ).fetchall()

    con.close()

    if location and location != "All Locations":
        if location in US_STATES:
            rows = [
                row for row in rows
                if row["country"] == "United States"
                and row["location"] == location
            ]
        else:
            rows = [
                row for row in rows
                if row["country"] == location
            ]

    return rows


# ============================================================
# MAIN PAGE
# ============================================================

@app.route("/", methods=["GET"])
def index():
    period = request.args.get("period", "1d")
    location = request.args.get("location", "All Locations")

    rows = get_incidents(period, location)

    us = [
        row for row in rows
        if row["country"] == "United States"
    ]

    international = [
        row for row in rows
        if row["country"] not in ("United States", "Unknown")
    ]

    unknown = [
        row for row in rows
        if row["country"] == "Unknown"
    ]

    killed = sum(row["status"] == "Killed" for row in rows)
    injured = sum(
        row["status"] == "Seriously Injured"
        for row in rows
    )
    review = sum(
        row["status"] == "Review Needed"
        for row in rows
    )

    # Used by the template for the summary cards if desired.
    killed_locations = sorted({
        row["location"] for row in rows
        if row["status"] == "Killed"
        and row["location"] != "Unknown"
    })

    injured_locations = sorted({
        row["location"] for row in rows
        if row["status"] == "Seriously Injured"
        and row["location"] != "Unknown"
    })

    all_locations = list(US_STATES.keys())

    international_locations = sorted(
        {
            country for country in INTL_COUNTRIES
            if country not in {
                "UK", "England", "Scotland", "Wales",
                "Northern Ireland", "Republic of Ireland",
            }
        }
        | {"United Kingdom"}
    )

    return render_template(
        "index.html",
        period=period,
        location=location,
        rows=rows,
        us=us,
        intl=international,
        unknown=unknown,
        killed=killed,
        injured=injured,
        review=review,
        total=len(rows),
        killed_locations=killed_locations,
        injured_locations=injured_locations,
        all_states=all_locations,
        international_locations=international_locations,
    )


# ============================================================
# SCAN BUTTON
# ============================================================

@app.route("/scan", methods=["POST"])
def do_scan():
    found, added = scan()

    flash(
        f"Scan finished: reviewed {found} candidate articles "
        f"and added {added} new unique incidents."
    )

    return redirect(
        url_for(
            "index",
            period=request.form.get("period", "1d"),
            location=request.form.get(
                "location",
                "All Locations",
            ),
        )
    )


# ============================================================
# START
# ============================================================

init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True,
    )
