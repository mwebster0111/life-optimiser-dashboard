"""
Shared helper functions for Life Optimiser GitHub Actions.
Handles Garmin, Notion, Google Calendar, Weather, and email sending.
"""

import os
import json
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# ── Timezone helper ──────────────────────────────────────────────
def copenhagen_now():
    """Return current datetime in Copenhagen (UTC+1 / UTC+2 DST)."""
    import subprocess
    result = subprocess.run(
        ["date", "+%Y-%m-%d %H:%M:%S %A %B %-d %Y %z"],
        capture_output=True, text=True,
        env={**os.environ, "TZ": "Europe/Copenhagen"}
    )
    parts = result.stdout.strip().split(" ", 2)
    date_str = parts[0]  # 2026-04-10
    return {
        "date": date_str,
        "full_output": result.stdout.strip(),
        "day_of_week": result.stdout.strip().split()[2],  # e.g. "Friday"
    }


def today_str():
    return copenhagen_now()["date"]


# ── Garmin (raw HTTP with Bearer token — no garth dependency) ────
class GarminClient:
    """Direct Garmin Connect API using raw HTTP requests with OAuth2 Bearer token.

    This bypasses garth entirely to avoid 429 errors on the token refresh
    endpoint from cloud IPs (GitHub Actions). The access token is passed in
    directly and used as-is — no automatic refresh attempts.
    """

    BASE = "https://connectapi.garmin.com"

    def __init__(self, access_token):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "NK": "NT",
        })

    def _get(self, path, **kwargs):
        """Make authenticated GET request."""
        url = f"{self.BASE}{path}"
        params = kwargs.get("params")
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_stats(self, date_str):
        return self._get(f"/usersummary-service/stats/{date_str}")

    def get_sleep_data(self, date_str):
        return self._get(f"/wellness-service/wellness/dailySleepData/{date_str}")

    def get_heart_rates(self, date_str):
        return self._get(f"/wellness-service/wellness/dailyHeartRate/{date_str}")

    def get_stress_data(self, date_str):
        return self._get(f"/wellness-service/wellness/dailyStress/{date_str}")

    def get_body_battery(self, date_str):
        return self._get(f"/wellness-service/wellness/bodyBattery/dates/{date_str}/{date_str}")

    def get_steps_data(self, date_str):
        return self._get(f"/wellness-service/wellness/dailySteps/{date_str}")

    def get_hrv_data(self, date_str):
        return self._get(f"/hrv-service/hrv/{date_str}")

    def get_activities_by_date(self, start_date, end_date):
        return self._get(
            f"/activitylist-service/activities/search/activities",
            params={"startDate": start_date, "endDate": end_date, "limit": 20}
        )

    def get_training_status(self, date_str):
        return self._get(f"/metrics-service/metrics/trainingstatus/aggregated/{date_str}")

    def get_training_readiness(self, date_str):
        return self._get(f"/metrics-service/metrics/trainingreadiness/{date_str}")

    def get_race_predictions(self):
        return self._get(f"/metrics-service/metrics/racepredictions")

    def get_endurance_score(self, date_str):
        return self._get(f"/metrics-service/metrics/endurancescore/{date_str}")

    def get_full_name(self):
        data = self._get("/userprofile-service/usersettings")
        return data.get("displayName", data.get("userName", "Unknown"))


def get_garmin_client():
    """Set up Garmin client with OAuth2 access token (no garth, no refresh).

    Accepts GARMIN_SESSION as either:
      - Full garth format: {"oauth1": {...}, "oauth2": {"access_token": "..."}}
      - Simple format: {"access_token": "..."}
      - Plain token string: "eyJ..."
    """
    session_raw = os.environ.get("GARMIN_SESSION", "").strip()

    if not session_raw:
        raise RuntimeError("GARMIN_SESSION secret not set.")

    # Determine token format
    access_token = None

    if session_raw.startswith("{"):
        # JSON format
        session_data = json.loads(session_raw)
        if "oauth2" in session_data:
            access_token = session_data["oauth2"].get("access_token")
        elif "access_token" in session_data:
            access_token = session_data["access_token"]
    else:
        # Plain token string
        access_token = session_raw

    if not access_token:
        raise RuntimeError(
            "Could not extract access_token from GARMIN_SESSION. "
            "Expected JSON with 'oauth2.access_token', 'access_token', or a plain token string."
        )

    client = GarminClient(access_token)
    try:
        name = client.get_full_name()
        print(f"Garmin: connected as {name}")
    except Exception as e:
        print(f"Garmin: connected (name lookup skipped: {e})")
    return client


def get_garmin_stats(client, date_str):
    """Pull comprehensive stats for a single date."""
    stats = client.get_stats(date_str)
    sleep = client.get_sleep_data(date_str)
    hr = client.get_heart_rates(date_str)
    stress = client.get_stress_data(date_str)
    bb = client.get_body_battery(date_str)
    steps_data = client.get_steps_data(date_str)
    hrv = client.get_hrv_data(date_str)
    return {
        "stats": stats,
        "sleep": sleep,
        "heart_rates": hr,
        "stress": stress,
        "body_battery": bb,
        "steps": steps_data,
        "hrv": hrv,
    }


def get_garmin_activities(client, start_date, end_date):
    """Get activities in a date range."""
    try:
        activities = client.get_activities_by_date(start_date, end_date)
        return activities or []
    except Exception:
        return []


def get_garmin_training_status(client):
    """Get training status, readiness, race predictions etc."""
    data = {}
    try:
        data["training_status"] = client.get_training_status(today_str())
    except Exception:
        data["training_status"] = None
    try:
        data["training_readiness"] = client.get_training_readiness(today_str())
    except Exception:
        data["training_readiness"] = None
    try:
        data["race_predictions"] = client.get_race_predictions()
    except Exception:
        data["race_predictions"] = None
    try:
        data["endurance_score"] = client.get_endurance_score(today_str())
    except Exception:
        data["endurance_score"] = None
    return data


def get_garmin_week_data(client, num_days=7):
    """Pull health trends for the last N days."""
    today = datetime.date.fromisoformat(today_str())
    trends = []
    for i in range(num_days - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        ds = d.isoformat()
        try:
            stats = client.get_stats(ds)
            sleep = client.get_sleep_data(ds)
            bb = client.get_body_battery(ds)
            hrv = client.get_hrv_data(ds)
            stress = client.get_stress_data(ds)

            # Extract sleep stages
            sleep_dto = sleep.get("dailySleepDTO", {})
            deep = round((sleep_dto.get("deepSleepSeconds", 0) or 0) / 3600, 1)
            light = round((sleep_dto.get("lightSleepSeconds", 0) or 0) / 3600, 1)
            rem = round((sleep_dto.get("remSleepSeconds", 0) or 0) / 3600, 1)
            awake = round((sleep_dto.get("awakeSleepSeconds", 0) or 0) / 3600, 1)
            sleep_score = sleep_dto.get("sleepScores", {}).get("overall", {}).get("value", 0)
            total_sleep = round((sleep_dto.get("sleepTimeSeconds", 0) or 0) / 3600, 1)

            # Body battery range
            bb_list = bb or []
            bb_vals = []
            if isinstance(bb_list, list):
                for entry in bb_list:
                    val = entry.get("chargedValue") or entry.get("drainedValue")
                    if val is not None:
                        bb_vals.append(val)
            elif isinstance(bb_list, dict):
                for entry in bb_list.get("bodyBatteryValuesArray", []) or []:
                    if len(entry) > 1 and entry[1] is not None:
                        bb_vals.append(entry[1])

            bb_high = max(bb_vals) if bb_vals else 0
            bb_low = min(bb_vals) if bb_vals else 0

            # HRV
            hrv_val = 0
            if hrv and isinstance(hrv, dict):
                summary = hrv.get("hrvSummary", {})
                if summary:
                    hrv_val = summary.get("lastNightAvg", 0) or summary.get("weeklyAvg", 0) or 0

            # Stress
            stress_avg = 0
            if stress and isinstance(stress, dict):
                stress_avg = stress.get("overallStressLevel", 0) or 0

            trends.append({
                "date": d.strftime("%a %-d"),
                "steps": stats.get("totalSteps", 0) or 0,
                "rhr": stats.get("restingHeartRate", 0) or 0,
                "stress": stress_avg,
                "bbHigh": bb_high,
                "bbLow": bb_low,
                "sleepScore": sleep_score or 0,
                "sleepHrs": total_sleep,
                "hrv": hrv_val,
                "deep": deep,
                "light": light,
                "rem": rem,
                "awake": awake,
            })
        except Exception as e:
            print(f"Warning: Could not get data for {ds}: {e}")
            trends.append({
                "date": d.strftime("%a %-d"),
                "steps": 0, "rhr": 0, "stress": 0,
                "bbHigh": 0, "bbLow": 0, "sleepScore": 0,
                "sleepHrs": 0, "hrv": 0, "deep": 0,
                "light": 0, "rem": 0, "awake": 0,
            })
    return trends


# ── Notion ───────────────────────────────────────────────────────
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

PAGE_IDS = {
    "hub": "33865ac0-f9ba-81a7-a57b-f5f4b69a2a84",
    "system_instructions": "33865ac0-f9ba-818d-a277-fedc659098d1",
    "calendar_interpreter": "33865ac0-f9ba-81e3-8c1f-ec7f94cd62c6",
    "garmin_interpreter": "33865ac0-f9ba-8110-a0bb-cbd2ef40251e",
    "weather_interpreter": "33865ac0-f9ba-81b0-92b1-f0b26392600e",
    "goals": "33865ac0-f9ba-8161-a294-c5cca2960f55",
    "personality": "33865ac0-f9ba-811f-9510-fdda82839fe7",
    "preferences": "33865ac0-f9ba-81d2-8c83-e3c88ac7df6d",
    "constraints": "33865ac0-f9ba-8184-9cdb-c0da4efb6b35",
    "weekly_plan": "33865ac0-f9ba-81cb-9e5f-f750343f6890",
    "daily_log": "33865ac0-f9ba-8187-9b07-c95b09a19328",
    "weekly_review": "33865ac0-f9ba-81e7-9fdb-e47b3bc2525b",
    "briefing_template": "33865ac0-f9ba-81e9-9998-c5f039bf15c3",
}


def notion_headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_get_page_content(page_id):
    """Fetch all blocks (content) from a Notion page."""
    blocks = []
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"
    while url:
        resp = requests.get(url, headers=notion_headers())
        data = resp.json()
        blocks.extend(data.get("results", []))
        if data.get("has_more"):
            url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100&start_cursor={data['next_cursor']}"
        else:
            url = None
    return blocks


def notion_blocks_to_text(blocks):
    """Convert Notion blocks to plain text."""
    lines = []
    for block in blocks:
        btype = block.get("type", "")
        content = block.get(btype, {})
        if "rich_text" in content:
            text = "".join(rt.get("plain_text", "") for rt in content["rich_text"])
            if btype.startswith("heading"):
                lines.append(f"\n{'#' * int(btype[-1])} {text}")
            elif btype == "bulleted_list_item":
                lines.append(f"- {text}")
            elif btype == "numbered_list_item":
                lines.append(f"• {text}")
            elif btype == "to_do":
                checked = "x" if content.get("checked") else " "
                lines.append(f"[{checked}] {text}")
            else:
                lines.append(text)
        elif btype == "divider":
            lines.append("---")
    return "\n".join(lines)


def notion_read_page(page_key):
    """Read a named page and return its text content."""
    page_id = PAGE_IDS[page_key]
    blocks = notion_get_page_content(page_id)
    return notion_blocks_to_text(blocks)


def notion_clear_and_write(page_id, markdown_text):
    """Clear a Notion page and write new content as blocks."""
    # First, get existing blocks
    existing = notion_get_page_content(page_id)

    # Delete existing blocks
    for block in existing:
        requests.delete(
            f"{NOTION_API}/blocks/{block['id']}",
            headers=notion_headers()
        )

    # Convert markdown to Notion blocks
    blocks = markdown_to_notion_blocks(markdown_text)

    # Append in batches of 100
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i+100]
        requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=notion_headers(),
            json={"children": batch}
        )


def markdown_to_notion_blocks(text):
    """Convert simple markdown to Notion block format."""
    blocks = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            blocks.append(_heading_block(3, stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(_heading_block(2, stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(_heading_block(1, stripped[2:]))
        elif stripped.startswith("- [ ] "):
            blocks.append(_todo_block(stripped[6:], False))
        elif stripped.startswith("- [x] "):
            blocks.append(_todo_block(stripped[6:], True))
        elif stripped.startswith("- "):
            blocks.append(_bullet_block(stripped[2:]))
        elif stripped == "---":
            blocks.append({"type": "divider", "divider": {}})
        else:
            blocks.append(_paragraph_block(stripped))
    return blocks


def _rich_text(text):
    return [{"type": "text", "text": {"content": text}}]


def _paragraph_block(text):
    return {"type": "paragraph", "paragraph": {"rich_text": _rich_text(text)}}


def _heading_block(level, text):
    key = f"heading_{level}"
    return {"type": key, key: {"rich_text": _rich_text(text)}}


def _bullet_block(text):
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich_text(text)}}


def _todo_block(text, checked):
    return {"type": "to_do", "to_do": {"rich_text": _rich_text(text), "checked": checked}}


# ── Google Calendar ──────────────────────────────────────────────
def get_gcal_events(days_ahead=3):
    """Get upcoming calendar events for the next N days."""
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GCAL_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GCAL_CLIENT_ID"],
        client_secret=os.environ["GCAL_CLIENT_SECRET"],
    )
    service = build("calendar", "v3", credentials=creds)

    today = datetime.date.fromisoformat(today_str())
    time_min = f"{today.isoformat()}T00:00:00Z"
    time_max = f"{(today + datetime.timedelta(days=days_ahead)).isoformat()}T23:59:59Z"

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])
    result = []
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date", ""))
        result.append({
            "summary": event.get("summary", "No title"),
            "start": start,
            "end": event["end"].get("dateTime", event["end"].get("date", "")),
            "all_day": "date" in event["start"],
        })
    return result


# ── Weather (Open-Meteo) ────────────────────────────────────────
def get_weather(days=7):
    """Get weather forecast for Copenhagen."""
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        "latitude=55.68&longitude=12.57"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
        f"&timezone=Europe/Copenhagen&forecast_days={days}"
    )
    resp = requests.get(url)
    data = resp.json()
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    rain = daily.get("precipitation_probability_max", [])
    codes = daily.get("weathercode", [])

    today_d = today_str()
    result = []
    for i, d in enumerate(dates):
        dt = datetime.date.fromisoformat(d)
        icon = weather_code_to_icon(codes[i] if i < len(codes) else 0)
        result.append({
            "day": dt.strftime("%a"),
            "icon": icon,
            "hi": round(highs[i]) if i < len(highs) else 0,
            "lo": round(lows[i]) if i < len(lows) else 0,
            "rain": rain[i] if i < len(rain) else 0,
            "isToday": d == today_d,
        })
    return result


def weather_code_to_icon(code):
    mapping = {
        0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
        45: "🌫️", 48: "🌫️",
        51: "🌦️", 53: "🌦️", 55: "🌧️",
        61: "🌧️", 63: "🌧️", 65: "🌧️",
        71: "🌨️", 73: "🌨️", 75: "🌨️",
        80: "🌦️", 81: "🌧️", 82: "🌧️",
        95: "⛈️", 96: "⛈️", 99: "⛈️",
    }
    return mapping.get(code, "🌤️")


# ── Email Sending ────────────────────────────────────────────────
def send_email(subject, html_body):
    """Send an HTML email via Gmail SMTP."""
    sender = "michaelwebbie@gmail.com"
    app_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Life Optimiser <{sender}>"
    msg["To"] = sender
    msg["Subject"] = subject

    msg.attach(MIMEText("This email is best viewed in an HTML-capable email client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, app_password)
        s.sendmail(sender, [sender], msg.as_string())

    print(f"Email sent: {subject}")


# ── Claude API ───────────────────────────────────────────────────
def ask_claude(prompt, max_tokens=4096):
    """Send a prompt to Claude and get a text response."""
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text
