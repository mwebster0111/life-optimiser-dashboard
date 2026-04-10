#!/usr/bin/env python3
"""
Life Optimiser — Data Refresh (runs at 11PM Copenhagen time)
Pulls Garmin, Calendar, and Weather data, then updates Notion pages.
"""

import json
import datetime
from helpers import (
    copenhagen_now, today_str, get_garmin_client, get_garmin_stats,
    get_garmin_activities, get_garmin_training_status, get_garmin_week_data,
    get_gcal_events, get_weather,
    notion_read_page, notion_clear_and_write, ask_claude, PAGE_IDS
)


def format_garmin_summary(stats, training, activities, trends):
    """Format Garmin data into a readable summary for Notion."""
    s = stats.get("stats", {})
    sleep = stats.get("sleep", {}).get("dailySleepDTO", {})

    lines = [
        f"# Garmin Data — {today_str()}",
        "",
        "## Today's Summary",
        f"- Steps: {s.get('totalSteps', 'N/A')}",
        f"- Resting HR: {s.get('restingHeartRate', 'N/A')} bpm",
        f"- Stress: {s.get('averageStressLevel', 'N/A')}",
        f"- Sleep Score: {sleep.get('sleepScores', {}).get('overall', {}).get('value', 'N/A')}",
        f"- Sleep Duration: {round((sleep.get('sleepTimeSeconds', 0) or 0) / 3600, 1)}h",
        f"- Calories: {s.get('totalKilocalories', 'N/A')}",
        "",
        "## Training Status",
    ]

    ts = training.get("training_status")
    if ts:
        lines.append(f"- Status: {ts.get('trainingStatusPhrase', 'N/A')}")
        lines.append(f"- VO2 Max: {ts.get('vo2MaxValue', 'N/A')}")
        lines.append(f"- Load: {ts.get('acuteTrainingLoad', 'N/A')} (acute) / {ts.get('chronicTrainingLoad', 'N/A')} (chronic)")

    rp = training.get("race_predictions")
    if rp:
        lines.append("")
        lines.append("## Race Predictions")
        for pred in (rp if isinstance(rp, list) else [rp]):
            if isinstance(pred, dict):
                for k, v in pred.items():
                    if "time" in k.lower() or "seconds" in k.lower():
                        lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append("## Recent Activities")
    for a in activities[:5]:
        name = a.get("activityName", "Activity")
        dist = round(a.get("distance", 0) / 1000, 2)
        dur_secs = a.get("duration", 0)
        dur = f"{int(dur_secs // 3600)}h{int((dur_secs % 3600) // 60)}m" if dur_secs > 3600 else f"{int(dur_secs // 60)}m"
        lines.append(f"- {name}: {dist}km in {dur}")

    lines.append("")
    lines.append("## 7-Day Trends")
    for t in trends:
        lines.append(f"- {t['date']}: {t['steps']} steps, Sleep {t['sleepHrs']}h (score {t['sleepScore']}), HRV {t['hrv']}, BB {t['bbLow']}-{t['bbHigh']}")

    return "\n".join(lines)


def format_calendar_summary(events):
    """Format calendar events for Notion."""
    lines = [f"# Calendar — {today_str()}", ""]
    if not events:
        lines.append("No upcoming events.")
        return "\n".join(lines)

    current_day = ""
    for event in events:
        start = event["start"]
        day_label = start[:10] if len(start) >= 10 else start
        if day_label != current_day:
            current_day = day_label
            try:
                dt = datetime.date.fromisoformat(day_label)
                lines.append(f"## {dt.strftime('%A, %B %-d')}")
            except Exception:
                lines.append(f"## {day_label}")
        if event["all_day"]:
            lines.append(f"- All day: {event['summary']}")
        else:
            time_str = start[11:16] if len(start) > 11 else start
            lines.append(f"- {time_str}: {event['summary']}")
    return "\n".join(lines)


def format_weather_summary(weather):
    """Format weather for Notion."""
    lines = [f"# Weather — Copenhagen", ""]
    for w in weather:
        today_marker = " (TODAY)" if w["isToday"] else ""
        lines.append(f"- {w['day']}{today_marker}: {w['icon']} {w['hi']}°/{w['lo']}° — {w['rain']}% rain")
    return "\n".join(lines)


def main():
    date_info = copenhagen_now()
    print(f"Data Refresh starting — {date_info['full_output']}")

    # Pull all data
    print("Connecting to Garmin...")
    garmin = get_garmin_client()
    today = today_str()
    seven_days_ago = (datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat()

    print("Pulling Garmin stats...")
    stats = get_garmin_stats(garmin, today)
    training = get_garmin_training_status(garmin)
    activities = get_garmin_activities(garmin, seven_days_ago, today)
    trends = get_garmin_week_data(garmin, 7)

    print("Pulling calendar events...")
    try:
        events = get_gcal_events(days_ahead=3)
    except Exception as e:
        print(f"Warning: Calendar fetch failed: {e}")
        events = []

    print("Pulling weather...")
    weather = get_weather(7)

    # Format summaries
    garmin_summary = format_garmin_summary(stats, training, activities, trends)
    calendar_summary = format_calendar_summary(events)
    weather_summary = format_weather_summary(weather)

    # Update Notion pages
    print("Updating Notion — Garmin Interpreter...")
    notion_clear_and_write(PAGE_IDS["garmin_interpreter"], garmin_summary)

    print("Updating Notion — Calendar Interpreter...")
    notion_clear_and_write(PAGE_IDS["calendar_interpreter"], calendar_summary)

    print("Updating Notion — Weather Interpreter...")
    notion_clear_and_write(PAGE_IDS["weather_interpreter"], weather_summary)

    # Update Daily Log with AI summary
    print("Generating AI daily log entry...")
    goals = notion_read_page("goals")
    constraints = notion_read_page("constraints")
    weekly_plan = notion_read_page("weekly_plan")

    day_name = date_info["day_of_week"]
    log_prompt = f"""You are the Life Optimiser AI assistant for Michael Webster, a student in Copenhagen.

Today is {day_name}, {today}.

Here is Michael's data for today:

GARMIN DATA:
{garmin_summary}

CALENDAR:
{calendar_summary}

WEATHER:
{weather_summary}

GOALS & PRIORITIES:
{goals}

CONSTRAINTS:
{constraints}

WEEKLY PLAN:
{weekly_plan}

Write a concise Daily Log entry for today in markdown format. Include:
1. A brief summary of how the day looks health-wise (sleep, recovery, stress)
2. Key activities/events for today and tomorrow
3. Any notable health trends from the 7-day data
4. Quick recommendations for tomorrow based on today's data

IMPORTANT: Everllence work is Monday-Friday only. Never schedule it on weekends.
Keep it concise and actionable. Use markdown formatting."""

    daily_log = ask_claude(log_prompt, max_tokens=2000)
    print("Updating Notion — Daily Log...")
    notion_clear_and_write(PAGE_IDS["daily_log"], f"# Daily Log — {day_name}, {today}\n\n{daily_log}")

    # On Sundays, also update Weekly Review
    if day_name == "Sunday":
        print("Sunday detected — generating Weekly Review...")
        review_prompt = f"""You are the Life Optimiser AI for Michael Webster.

Today is Sunday {today} — end of the week. Generate a Weekly Review based on this data:

GARMIN 7-DAY TRENDS:
{garmin_summary}

WEEKLY PLAN:
{weekly_plan}

GOALS:
{goals}

Write a concise weekly review in markdown:
1. Health & fitness summary (sleep quality trend, steps, training load)
2. What went well this week
3. Areas for improvement
4. Suggestions for next week
Keep it actionable and positive."""

        weekly_review = ask_claude(review_prompt, max_tokens=2000)
        notion_clear_and_write(PAGE_IDS["weekly_review"], f"# Weekly Review — Week of {today}\n\n{weekly_review}")

    print("Data refresh complete!")


if __name__ == "__main__":
    main()
