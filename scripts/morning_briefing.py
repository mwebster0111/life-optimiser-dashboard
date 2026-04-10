#!/usr/bin/env python3
"""
Life Optimiser — Morning Briefing Email (runs at 8:30AM Copenhagen time)
Reads Notion pages, pulls fresh Garmin data, generates HTML email via Claude, sends via SMTP.
"""

import os
import json
import datetime
from helpers import (
    copenhagen_now, today_str, get_garmin_client, get_garmin_stats,
    get_garmin_week_data, get_garmin_activities, get_garmin_training_status,
    get_gcal_events, get_weather,
    notion_read_page, ask_claude, send_email
)


def main():
    date_info = copenhagen_now()
    day_name = date_info["day_of_week"]
    today = today_str()
    print(f"Morning Briefing starting — {date_info['full_output']}")

    # Read all Notion context
    print("Reading Notion pages...")
    pages = {}
    for key in ["goals", "constraints", "preferences", "personality",
                "weekly_plan", "daily_log", "briefing_template"]:
        try:
            pages[key] = notion_read_page(key)
        except Exception as e:
            print(f"Warning: Could not read {key}: {e}")
            pages[key] = ""

    # Pull fresh Garmin data (especially sleep from last night)
    print("Connecting to Garmin...")
    garmin = get_garmin_client()
    stats = get_garmin_stats(garmin, today)
    training = get_garmin_training_status(garmin)
    trends = get_garmin_week_data(garmin, 7)
    seven_days_ago = (datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat()
    activities = get_garmin_activities(garmin, seven_days_ago, today)

    # Pull calendar and weather
    print("Pulling calendar and weather...")
    try:
        events = get_gcal_events(days_ahead=2)
    except Exception as e:
        print(f"Warning: Calendar failed: {e}")
        events = []

    weather = get_weather(3)

    # Format data for prompt
    sleep_dto = stats.get("sleep", {}).get("dailySleepDTO", {})
    sleep_score = sleep_dto.get("sleepScores", {}).get("overall", {}).get("value", "N/A")
    sleep_hours = round((sleep_dto.get("sleepTimeSeconds", 0) or 0) / 3600, 1)
    deep = round((sleep_dto.get("deepSleepSeconds", 0) or 0) / 3600, 1)
    light = round((sleep_dto.get("lightSleepSeconds", 0) or 0) / 3600, 1)
    rem = round((sleep_dto.get("remSleepSeconds", 0) or 0) / 3600, 1)

    s = stats.get("stats", {})
    rhr = s.get("restingHeartRate", "N/A")

    ts = training.get("training_status") or {}
    tr = training.get("training_readiness") or {}

    bb = stats.get("body_battery", [])
    bb_vals = []
    if isinstance(bb, list):
        for entry in bb:
            val = entry.get("chargedValue") or entry.get("drainedValue")
            if val is not None:
                bb_vals.append(val)
    elif isinstance(bb, dict):
        for entry in bb.get("bodyBatteryValuesArray", []) or []:
            if len(entry) > 1 and entry[1] is not None:
                bb_vals.append(entry[1])
    current_bb = bb_vals[-1] if bb_vals else "N/A"

    hrv_data = stats.get("hrv", {})
    hrv_val = 0
    if hrv_data and isinstance(hrv_data, dict):
        summary = hrv_data.get("hrvSummary", {})
        if summary:
            hrv_val = summary.get("lastNightAvg", 0) or summary.get("weeklyAvg", 0) or 0

    events_str = ""
    for e in events:
        time_str = e["start"][11:16] if len(e["start"]) > 11 and not e["all_day"] else "All day"
        events_str += f"- {time_str}: {e['summary']}\n"

    weather_str = ""
    for w in weather:
        marker = " (TODAY)" if w["isToday"] else ""
        weather_str += f"- {w['day']}{marker}: {w['icon']} {w['hi']}°/{w['lo']}°, {w['rain']}% rain\n"

    recent_activities = ""
    for a in activities[:5]:
        name = a.get("activityName", "Activity")
        dist = round(a.get("distance", 0) / 1000, 2)
        recent_activities += f"- {name}: {dist}km\n"

    trends_summary = ""
    for t in trends[-3:]:
        trends_summary += f"- {t['date']}: {t['steps']} steps, Sleep {t['sleepHrs']}h (score {t['sleepScore']}), HRV {t['hrv']}, BB {t['bbLow']}-{t['bbHigh']}\n"

    prompt = f"""You are the Life Optimiser AI for Michael Webster, a student athlete in Copenhagen.

Today is {day_name}, {today}. Generate his morning briefing email.

LAST NIGHT'S SLEEP:
- Score: {sleep_score}
- Total: {sleep_hours}h (Deep: {deep}h, Light: {light}h, REM: {rem}h)
- Resting HR: {rhr} bpm
- HRV: {hrv_val}
- Current Body Battery: {current_bb}

TRAINING STATUS:
- Status: {ts.get('trainingStatusPhrase', 'N/A')}
- VO2 Max: {ts.get('vo2MaxValue', 'N/A')}
- Training Readiness: {json.dumps(tr, default=str)[:300]}

RECENT 3-DAY TRENDS:
{trends_summary}

RECENT ACTIVITIES:
{recent_activities}

TODAY'S SCHEDULE:
{events_str if events_str else "No events scheduled."}

WEATHER:
{weather_str}

GOALS & PRIORITIES:
{pages['goals']}

CONSTRAINTS:
{pages['constraints']}

WEEKLY PLAN:
{pages['weekly_plan']}

DAILY LOG (from last night):
{pages['daily_log']}

PREFERENCES:
{pages['preferences']}

BRIEFING TEMPLATE:
{pages['briefing_template']}

Generate a complete, self-contained HTML email for the morning briefing. The email should:

1. Use a clean, modern design with light background (#f0f2f5) and white cards
2. Use Inter font family (or system sans-serif fallback)
3. Be fully responsive for mobile viewing
4. Include these sections:
   - Header with greeting and date
   - Sleep & Recovery summary (with color-coded indicators)
   - Today's Schedule
   - Weather snapshot
   - Key Focus Areas for today (from weekly plan)
   - Training recommendation (based on readiness/recovery)
   - Quick tips/suggestions (2-3 actionable items)
   - A motivational close

CRITICAL RULES:
- Today is {day_name} — use this EXACT day name, do NOT compute it yourself
- Everllence work is Monday-Friday only, NEVER on weekends
- Use REAL data from above — never fabricate numbers
- Keep it concise — this is a quick morning read, not a report
- Color code health metrics: green=good, orange=moderate, red=needs attention
- The HTML must be complete and self-contained (inline CSS)
- Do NOT include ```html or ``` markers — return RAW HTML only

Return ONLY the complete HTML email, starting with <!DOCTYPE html>."""

    print("Generating email via Claude...")
    html = ask_claude(prompt, max_tokens=4000)

    # Clean up if wrapped in code block
    if html.startswith("```"):
        html = html.split("\n", 1)[1]  # Remove first line
        if html.endswith("```"):
            html = html[:-3]

    # Format subject line
    subject = f"Good Morning Michael — {day_name}, {today}"

    # Send email
    print("Sending email...")
    send_email(subject, html)
    print("Morning briefing sent!")


if __name__ == "__main__":
    main()
