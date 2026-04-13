from fastapi import APIRouter, Request, Depends
from datetime import datetime, timedelta

import database
from routes.users import get_current_user

router = APIRouter()

@router.get("/scheduling-insights")
async def get_insights(request: Request, username: str = Depends(get_current_user)):
    now = datetime.now()
    current_weekday = now.strftime("%A")

    patterns = analyze_scheduling_patterns(username)

    current_hour = now.hour
    start_of_week, end_of_week = get_current_week_range()

    insights = []

    # Check each recurring pattern
    for pattern in patterns["recurring_patterns"]:
        title = pattern["title"]
        usual_day = pattern["day"]
        typical_hour = pattern["typical_hour"]
        occurrences = pattern["occurrences"]

        # Check if this meeting is scheduled for the current week
        has_current_week_occurrence = False
        for occ in occurrences:
            if start_of_week <= occ["datetime"] <= end_of_week:
                has_current_week_occurrence = True
                break

        # Format time nicely
        if typical_hour < 12:
            time_str = f"{typical_hour}am" if typical_hour > 0 else "12am"
        elif typical_hour == 12:
            time_str = "12pm"
        else:
            time_str = f"{typical_hour - 12}pm"

        # If it's the usual day, before the usual time, and no meeting scheduled this week
        if usual_day == current_weekday and not has_current_week_occurrence and current_hour < typical_hour:
            insights.append({
                "priority": 1,
                "text": f"You usually have {title} on {usual_day}s at {time_str}"
            })
        # If it's before the usual day this week and not scheduled
        elif not has_current_week_occurrence:
            day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            usual_day_idx = day_order.index(usual_day)
            current_day_idx = day_order.index(current_weekday)

            if usual_day_idx > current_day_idx:
                # Upcoming day this week, meeting not scheduled
                insights.append({
                    "priority": 2,
                    "text": f"You usually have {title} on {usual_day}s"
                })

    # Check for upcoming meetings today
    today_str = now.strftime("%Y-%m-%d")
    upcoming_today = []
    all_events = database.get_user_events(username)
    for event in all_events:
        if event["start_time"].startswith(today_str):
            try:
                event_time = datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
                if event_time > now:
                    upcoming_today.append(event)
            except (ValueError, TypeError):
                continue

    if upcoming_today:
        upcoming_today.sort(key=lambda e: e["start_time"])
        next_event = upcoming_today[0]
        event_time = datetime.strptime(next_event["start_time"], "%Y-%m-%d %H:%M:%S")
        time_until = event_time - now
        mins = int(time_until.total_seconds() / 60)
        if mins <= 60:
            insights.append({
                "priority": 0,  # Highest priority
                "text": f"You have '{next_event['title']}' in {mins} minutes"
            })

    # Sort by priority and return the top insight
    if insights:
        insights.sort(key=lambda x: x["priority"])
        msg = insights[0]["text"]
    else:
        # Default based on time
        if current_hour < 9:
            msg = "Good morning! What would you like to schedule today?"
        elif current_weekday not in ("Saturday", "Sunday") and current_hour >= 14:
            msg = f"{current_weekday} afternoon - good time for focused work"
        else:
            msg = "What would you like to add to your calendar?"
    return {"insight": msg}

def analyze_scheduling_patterns(username):
    """
    Analyze calendar events to infer user scheduling patterns.
    Detects implicit recurring patterns even for non-recurring events.
    """
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Group events by title + day of week to detect recurring patterns
    title_day_patterns = {}  # {(title, day_of_week): [list of events]}

    all_events = database.get_user_events(username)
    for event in all_events:
        try:
            start_dt = datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
            day_of_week = day_names[start_dt.weekday()]
            hour = start_dt.hour
            title = event["title"]

            # Track title + day patterns
            key = (title, day_of_week)
            if key not in title_day_patterns:
                title_day_patterns[key] = []
            title_day_patterns[key].append({
                "event": event,
                "datetime": start_dt,
                "hour": hour
            })

        except (ValueError, TypeError):
            continue
        
    # Identify recurring patterns (2+ occurrences on the same day of week)
    recurring_patterns = []
    for (title, day), occurrences in title_day_patterns.items():
        if len(occurrences) >= 2:
            # Calculate typical time
            hours = [o["hour"] for o in occurrences]
            typical_hour = max(set(hours), key=hours.count)  # Most common hour

            recurring_patterns.append({
                "title": title,
                "day": day,
                "typical_hour": typical_hour,
                "occurrences": occurrences,
                "count": len(occurrences)
            })

    return {
        "recurring_patterns": recurring_patterns,
        "title_day_patterns": title_day_patterns
    }

def get_current_week_range():
    """Get the start and end dates of the current week (Monday to Sunday)."""
    now = datetime.now()
    start_of_week = now - timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_week = start_of_week + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start_of_week, end_of_week