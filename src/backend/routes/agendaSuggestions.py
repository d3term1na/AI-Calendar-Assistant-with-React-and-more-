from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime
import requests

import database
from routes.users import get_current_user
from llm import OLLAMA_URL, MODEL_NAME

router = APIRouter()

@router.get("/agenda-suggestions")
async def get_agenda_suggestions(request: Request, username: str = Depends(get_current_user)):
    """Get agenda suggestions for upcoming recurring meetings based on past notes."""
    suggestions = get_upcoming_recurring_meetings(username)

    formatted = []
    for item in suggestions:
        formatted.append({
            "event_id": item["upcoming_event"]["event_id"],
            "event_title": item["upcoming_event"]["title"],
            "event_time": item["upcoming_event"]["start_time"],
            "last_meeting_date": item["last_occurrence"]["start_time"],
            "suggested_agenda": item["suggested_agenda"],
            "recurrence_group": item["upcoming_event"].get("recurrence_group")
        })
    return {"suggestions": formatted}

def get_upcoming_recurring_meetings(username):
    """
    Get recurring meetings that are coming up (in the future)
    that have a past occurrence with notes.
    Returns list of {event, last_occurrence_notes, suggested_agenda}
    """
    now = datetime.now()

    all_events = database.get_user_events(username)

    # Group events by recurrence_group
    recurrence_groups = {}
    for event in all_events:
        group_id = event.get("recurrence_group")
        if group_id:
            if group_id not in recurrence_groups:
                recurrence_groups[group_id] = []
            recurrence_groups[group_id].append(event)

    suggestions = []

    for group_id, events in recurrence_groups.items():
        # Separate past and upcoming events using full datetime comparison
        past_events = []
        upcoming_events = []

        for event in events:
            try:
                event_datetime = datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
                # Compare full datetime, not just date
                # An event that started before now is "past"
                if event_datetime < now:
                    past_events.append(event)
                else:
                    upcoming_events.append(event)
            except (ValueError, TypeError):
                continue

        # Sort past events by date descending (most recent first)
        past_events.sort(key=lambda e: e["start_time"], reverse=True)
        # Sort upcoming events by date ascending (soonest first)
        upcoming_events.sort(key=lambda e: e["start_time"])

        # Find the immediate last past occurrence with notes
        last_with_notes = None
        for event in past_events:
            if event.get("notes") and event["notes"].strip():
                last_with_notes = event
                break

        # If we have an upcoming event and a past event with notes, generate suggestion
        if upcoming_events and last_with_notes:
            upcoming_event = upcoming_events[0]

            notes = last_with_notes["notes"]

            # Use LLM to generate agenda suggestions from notes
            suggested_agenda = generate_agenda_from_notes(
                last_with_notes["title"],
                notes
            )

            suggestions.append({
                "upcoming_event": upcoming_event,
                "last_occurrence": last_with_notes,
                "suggested_agenda": suggested_agenda
            })

    return suggestions

def generate_agenda_from_notes(meeting_title, notes):
    """Use LLM to extract agenda items/follow-ups from previous meeting notes."""
    extraction_prompt = f"""Based on these meeting notes from a previous "{meeting_title}" meeting, suggest 2-3 concise agenda items or follow-ups for the next meeting.

Previous meeting notes:
{notes}

Return ONLY a brief bullet list of suggested agenda items (no explanations, just the items). Focus on:
- Action items that were mentioned
- Topics that need follow-up
- Unresolved issues

Keep each item under 15 words. Format as a simple bullet list starting with "-"."""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract actionable agenda items from meeting notes. Be concise."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        response_text = res.json()["message"]["content"].strip()
        return response_text
    except Exception as e:
        return extract_simple_agenda(notes)


def extract_simple_agenda(notes):
    """Fallback: Extract action items without LLM."""
    items = []

    # Look for common action item patterns
    patterns = [
        "follow up",
        "action item",
        "need to",
        "should",
        "will"
    ]

    sentences = notes.replace(". ", ".\n").split("\n")
    for sentence in sentences:
        sentence = sentence.strip()
        sentence_lower = sentence.lower()
        if any(pattern in sentence_lower for pattern in patterns):
            if len(sentence) > 10:
                items.append(f"- {sentence[:80]}")
                if len(items) >= 3:
                    break

    return "\n".join(items) if items else "- Review previous meeting notes"