from fastapi import APIRouter, Request, Depends
import requests
from datetime import datetime, timedelta
import re
import json
from zoneinfo import ZoneInfo
from sentence_transformers import util
import uuid

from routes.users import get_current_user
from llm import OLLAMA_URL, MODEL_NAME, EMBED_MODEL
import database

router = APIRouter()

@router.post("/chat")
async def chat_endpoint(request: Request, username: str = Depends(get_current_user)):
    body = await request.json()
    user_message = body.get("message", "")

    reply, metadata = agent_process(username, user_message)
    return {
        "reply": reply,
        "requires_clarification": False,
        "metadata": metadata
    }


def agent_process(username, user_message):
    # Get conversation history from database
    history = database.get_conversation_history(username)
    history.append({"user": user_message})

    reply = ""
    metadata = {}

    # Classify intent using LLM
    intent = classify_intent(user_message)
    metadata["intent"] = intent

    if intent == "CREATE":
        details = extract_event_details(user_message)

        # Check for time conflicts before creating
        conflicts = check_time_conflict(username, details["start_time"], details["end_time"])
        if conflicts:
            conflict_msg = format_conflict_message(conflicts)
            # Format the requested time nicely
            time_str = details['start_time']
            try:
                parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
            except:
                pass
            reply = f"I can't schedule '{details['title']}' for {time_str}. {conflict_msg} Would you like to pick a different time?"
            metadata["conflict"] = True
            metadata["conflicting_events"] = conflicts
        else:
            event = database.create_event(
                username=username,
                title=details["title"],
                start_time=details["start_time"],
                end_time=details["end_time"],
                participants=details.get("participants", [])
            )
            # Build natural language response
            participants_str = ""
            if event.get('participants'):
                participants_str = f" with {', '.join(event['participants'])}"
            # Format time nicely
            time_str = event['start_time']
            try:
                parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
            except:
                pass
            reply = f"Got it! Scheduled '{event['title']}'{participants_str} for {time_str}."
            metadata["events_created"] = [event]
            # Store richer event info in RAG
            participants_info = f" with {', '.join(event['participants'])}" if event.get('participants') else ""
            content = f"Event: {event['title']}{participants_info} scheduled for {event['start_time']}"
            store_event_embedding(event["event_id"], content)
    elif intent == "CREATE_RECURRING":
        details = extract_recurring_details(user_message)

        # Calculate the dates for recurring events
        dates = calculate_recurring_dates(details)

        if not dates:
            reply = "I couldn't determine the recurring schedule. Please specify the day and frequency."
        else:
            created_events = []
            skipped_dates = []  # Dates skipped due to conflicts
            time_str = details.get("time", "09:00:00")
            duration = details.get("duration_minutes", 45)

            # Generate a unique recurrence_group ID for this series
            recurrence_group = str(uuid.uuid4())[:8]
            event_participants = details.get("participants").copy()
            event_participants.append(username)
            for event_date in dates:
                # Build start and end times
                start_datetime = f"{event_date.strftime('%Y-%m-%d')} {time_str}"
                try:
                    start_datetime = datetime.strptime(start_datetime, "%Y-%m-%d %H:%M:%S")
                except:
                    start_datetime = datetime.strptime(f"{event_date.strftime('%Y-%m-%d')} 09:00:00", "%Y-%m-%d %H:%M:%S")
                end_datetime = start_datetime + timedelta(minutes=duration)

                start_time_str = start_datetime.strftime("%Y-%m-%d %H:%M:%S")
                end_time_str = end_datetime.strftime("%Y-%m-%d %H:%M:%S")

                # Check for conflicts
                conflicts = check_time_conflict(username, start_time_str, end_time_str)
                if conflicts:
                    skipped_dates.append({
                        "date": event_date,
                        "conflicts": conflicts
                    })
                    continue  # Skip this occurrence
                
                event = database.create_event(
                    username=username,
                    title=details["title"],
                    start_time=start_time_str,
                    end_time=end_time_str,
                    participants=event_participants,
                    recurrence_group=recurrence_group
                )
                created_events.append(event)
                # Store in RAG
                participants_info = f" with {', '.join(event['participants'])}" if event.get('participants') else ""
                content = f"Event: {event['title']}{participants_info} scheduled for {event['start_time']}"
                store_event_embedding(event["event_id"], content)

            # Build natural language response
            num_events = len(created_events)
            frequency = details.get("frequency", "weekly")
            day_of_week = details.get("day_of_week", "")
            participants_str = ""
            if event.get('participants'):
                participants_str = f" with {', '.join(details.get("participants"))}"
            # Format time nicely
            try:
                time_parsed = datetime.strptime(time_str, "%H:%M:%S")
                time_formatted = time_parsed.strftime("%I:%M %p").lstrip("0")
            except:
                time_formatted = time_str

            if num_events == 0:
                # All dates had conflicts
                conflict_dates = [s["date"].strftime("%B %d") for s in skipped_dates]
                reply = f"I couldn't schedule any '{details['title']}' events. All requested times conflict with existing events on {', '.join(conflict_dates)}."
                metadata["conflict"] = True
            elif skipped_dates:
                # Some dates had conflicts
                first_date = created_events[0]["start_time"].split(" ")[0]
                first_date_str = datetime.strptime(first_date, "%Y-%m-%d").strftime("%B %d")
                skipped_date_strs = [s["date"].strftime("%B %d") for s in skipped_dates]

                if frequency == "weekly" and day_of_week:
                    reply = f"I've scheduled '{details['title']}'{participants_str} for every {day_of_week.capitalize()} at {time_formatted}, starting {first_date_str} ({num_events} events). Skipped {', '.join(skipped_date_strs)} due to conflicts."
                else:
                    reply = f"Created {num_events} '{details['title']}' events{participants_str} starting {first_date_str}. Skipped {', '.join(skipped_date_strs)} due to conflicts."

                metadata["events_created"] = created_events
                metadata["skipped_due_to_conflict"] = skipped_dates
            else:
                # No conflicts
                first_date = dates[0]
                first_date_str = first_date.strftime("%B %d")
                
                if frequency == "weekly" and day_of_week:
                    reply = f"Done! I've scheduled '{details['title']}'{participants_str} for every {day_of_week.capitalize()} at {time_formatted}, starting {first_date_str} ({num_events} events total)."
                elif frequency == "daily":
                    reply = f"Done! I've scheduled '{details['title']}'{participants_str} daily at {time_formatted}, starting {first_date_str} ({num_events} events total)."
                else:
                    reply = f"Done! I've created {num_events} recurring '{details['title']}' events{participants_str} starting {first_date_str} at {time_formatted}."

                metadata["events_created"] = created_events
    elif intent == "DELETE":
        filters = extract_query_filters(user_message)
        events = database.query_events(
            username=username,
            start_date=filters["start_date"],
            end_date=filters["end_date"],
            participants=filters["participants"],
            keyword=filters["keyword"]
        )

        # Fallback: if no events found with keyword, try without keyword
        if not events and filters["keyword"]:
            events = database.query_events(
                username=username,
                start_date=filters["start_date"],
                end_date=filters["end_date"],
                participants=filters["participants"],
                keyword=None
            )

        # Fallback: if still no events, try without date filter
        if not events and (filters["start_date"] or filters["end_date"]):
            events = database.query_events(
                username=username,
                participants=filters["participants"],
                keyword=filters["keyword"]
            )

        if events:
            deleted = database.delete_event(events[0]["event_id"])
            reply = f"Deleted event: {deleted['title']} ({deleted['start_time']})"
            metadata["events_deleted"] = [deleted]
        else:
            reply = "No matching events found to delete."
    elif intent == "QUERY":
        filters = extract_query_filters(user_message)

        # Default to current datetime onwards when no date filters are specified
        query_start = filters["start_date"]
        if not query_start and not filters["end_date"]:
            query_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        events = database.query_events(
            username=username,
            start_date=query_start,
            end_date=filters["end_date"],
            participants=filters["participants"],
            keyword=filters["keyword"]
        )

        # Detect if user is asking a specific question about event details
        msg_lower = user_message.lower()
        is_asking_participants = any(phrase in msg_lower for phrase in ["who was", "who is", "who are", "who's in", "with whom", "participants", "attendees", "who attended", "who will be"])
        is_asking_notes = any(phrase in msg_lower for phrase in ["what notes", "what was discussed", "notes from", "summary of"])

        if events:
            if is_asking_participants and len(events) == 1:
                # User is asking about participants for a specific event
                event = events[0]
                participants = event.get("participants", [])
                if participants:
                    reply = f"'{event['title']}' on {event['start_time'].split(' ')[0]} had these participants: {', '.join(participants)}."
                else:
                    reply = f"'{event['title']}' on {event['start_time'].split(' ')[0]} had no participants listed - it appears to be a solo event."
            elif is_asking_notes and len(events) == 1:
                # User is asking about notes for a specific event
                event = events[0]
                notes = event.get("notes", "")
                if notes:
                    reply = f"Notes from '{event['title']}': {notes}"
                else:
                    reply = f"No notes recorded for '{event['title']}'."
            else:
                # Default: list events
                event_lines = []
                for e in events:
                    line = f"- {e['title']} at {e['start_time']}"
                    if e.get("participants"):
                        line += f" (with {', '.join(e['participants'])})"
                    event_lines.append(line)
                reply = "Your events:\n" + "\n".join(event_lines)
            metadata["events_queried"] = events
        else:
            reply = "No events found matching your criteria."
    elif intent == "UPDATE":
        identifier = extract_event_identifier(user_message)
        events = database.query_events(
            username=username,
            start_date=identifier["current_date"],
            end_date=identifier["current_date"],
            participants=identifier["participants"],
            keyword=identifier["keyword"]
        )
        # Fallback: if still no events, try without date filter
        if not events and identifier["current_date"]:
            events = database.query_events(
                username=username,
                participants=identifier["participants"],
                keyword=identifier["keyword"]
            )

        # Fallback: if no events found with keyword, try without keyword
        if not events and identifier["keyword"]:
            events = database.query_events(
                username=username,
                start_date=identifier["current_date"],
                end_date=identifier["current_date"],
                participants=identifier["participants"],
                keyword=None
            )
            
        if not events:
            reply = "No matching events found to update."
        else:
            event = events[0]
            update_details = extract_update_details(user_message)

            # Build updates dict with only non-null values
            updates = {}
            if update_details["new_title"] and update_details["new_title"] != event["title"]:
                updates["title"] = update_details["new_title"]
            if update_details["new_start_time"]:
                updates["start_time"] = update_details["new_start_time"]
                if not update_details["new_end_time"]:
                    time_delta = datetime.strptime(event["end_time"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
                    updates["end_time"] = (datetime.strptime(updates["start_time"], "%Y-%m-%d %H:%M:%S") + time_delta).strftime("%Y-%m-%d %H:%M:%S")
            if update_details["new_end_time"]:
                updates["end_time"] = update_details["new_end_time"]
            if update_details["new_participants"] is not None:
                updates["participants"] = update_details["new_participants"] + [username]
            elif update_details["add_participants"]:
                current = event.get("participants", [])
                updates["participants"] = current + update_details["add_participants"]
            elif update_details["remove_participants"]:
                current = event.get("participants", [])
                updates["participants"] = [p for p in current if p not in update_details["remove_participants"]]

            # Check for time conflicts if rescheduling
            if "start_time" in updates or "end_time" in updates:
                new_start = updates.get("start_time", event["start_time"])
                new_end = updates.get("end_time", event["end_time"])

                # Exclude the current event from conflict check (don't conflict with itself)
                conflicts = check_time_conflict(username, new_start, new_end, exclude_event_id=event["event_id"])

                if conflicts:
                    conflict_msg = format_conflict_message(conflicts)
                    # Format the requested time nicely
                    time_str = new_start
                    try:
                        parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                        time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
                    except:
                        pass
                    reply = f"I can't reschedule '{event['title']}' to {time_str}. {conflict_msg} Would you like to pick a different time?"
                    metadata["conflict"] = True
                    metadata["conflicting_events"] = conflicts
                else:
                    # No conflicts, proceed with update
                    updated = database.update_event(event["event_id"], **updates)
                    # Build natural language response
                    change_parts = []
                    if "start_time" in updates:
                        time_str = updates['start_time']
                        try:
                            parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                            time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
                        except:
                            pass
                        change_parts.append(f"rescheduled it to {time_str}")
                    if "title" in updates:
                        change_parts.append(f"renamed it to '{updates['title']}'")
                    if "participants" in updates:
                        change_parts.append("updated the participants")

                    if change_parts:
                        reply = f"Done! I've {' and '.join(change_parts)} for '{event['title']}'."
                    else:
                        reply = f"Updated '{updated['title']}' successfully."
                    metadata["events_updated"] = [updated]
            elif updates:
                # No time change, just update other fields (no conflict check needed)
                updated = database.update_event(event["event_id"], **updates)
                change_parts = []
                if "title" in updates:
                    change_parts.append(f"renamed it to '{updates['title']}'")
                if "participants" in updates:
                    change_parts.append("updated the participants")

                if change_parts:
                    reply = f"Done! I've {' and '.join(change_parts)} for '{event['title']}'."
                else:
                    reply = f"Updated '{updated['title']}' successfully."
                metadata["events_updated"] = [updated]
            else:
                reply = "I couldn't understand what you want to change. Please specify the new time, title, or participants."
    elif intent == "ADD_NOTES":
        notes_details = extract_notes_details(user_message)

        # Find the event to add notes to
        events = database.query_events(
            username=username,
            start_date=notes_details["event_date"],
            end_date=notes_details["event_date"],
            participants=notes_details["participants"],
            keyword=notes_details["keyword"]
        )
        # Fallback: try without date filter
        if not events and notes_details["keyword"]:
            events = database.query_events(
                username=username,
                participants=notes_details["participants"],
                keyword=notes_details["keyword"]
            )

        # Fallback: get all events if still none found
        if not events:
            reply = "No matching events found to add notes to."
        elif events and notes_details["notes"]:
            event = events[0]
            # Append to existing notes or create new
            existing_notes = event.get("notes", "")
            if existing_notes:
                new_notes = existing_notes + "\n" + notes_details["notes"]
            else:
                new_notes = notes_details["notes"]

            updated = database.update_event(event["event_id"], notes=new_notes)
            reply = f"Added notes to '{event['title']}': \"{notes_details['notes']}\""
            metadata["events_updated"] = [updated]
            # Store notes in RAG for future questions
            notes_content = f"Meeting '{event['title']}' on {event['start_time']}: {notes_details['notes']}"
            store_event_embedding(event["event_id"], notes_content)
        elif not notes_details["notes"]:
            reply = "I couldn't understand what notes you want to add. Please try again."
    elif intent == "BULK_RESCHEDULE":
        bulk_details = extract_bulk_operation_details(user_message)
        source_date = bulk_details["source_date"]
        destination_date = bulk_details["destination_date"]

        if not source_date or not destination_date:
            reply = "I couldn't understand which dates you want to move events between. Please specify the source and destination dates."
        else:
            # Find all events on the source date
            events_to_move = database.query_events(username=username, start_date=source_date, end_date=source_date)

            if not events_to_move:
                # Format date nicely
                try:
                    source_parsed = datetime.strptime(source_date, "%Y-%m-%d")
                    source_str = source_parsed.strftime("%B %d")
                except:
                    source_str = source_date
                reply = f"You don't have any events scheduled on {source_str}."
            else:
                moved_events = []
                conflict_events = []

                for event in events_to_move:
                    # Calculate new start and end times
                    try:
                        old_start = datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
                        old_end = datetime.strptime(event["end_time"], "%Y-%m-%d %H:%M:%S")
                        duration = old_end - old_start

                        # Keep the same time, just change the date
                        new_start = datetime.strptime(f"{destination_date} {old_start.strftime('%H:%M:%S')}", "%Y-%m-%d %H:%M:%S")
                        new_end = new_start + duration

                        new_start_str = new_start.strftime("%Y-%m-%d %H:%M:%S")
                        new_end_str = new_end.strftime("%Y-%m-%d %H:%M:%S")

                        # Check for conflicts on the destination date
                        conflicts = check_time_conflict(username, new_start_str, new_end_str, exclude_event_id=event["event_id"])

                        if conflicts:
                            conflict_events.append({
                                "event": event,
                                "conflicts": conflicts
                            })
                        else:
                            # Move the event
                            updated = database.update_event(event["event_id"], start_time=new_start_str, end_time=new_end_str)
                            moved_events.append(updated)
                    except Exception as e:
                        continue

                # Build response
                try:
                    source_parsed = datetime.strptime(source_date, "%Y-%m-%d")
                    dest_parsed = datetime.strptime(destination_date, "%Y-%m-%d")
                    source_str = source_parsed.strftime("%B %d")
                    dest_str = dest_parsed.strftime("%B %d")
                except:
                    source_str = source_date
                    dest_str = destination_date

                if moved_events and not conflict_events:
                    event_names = [f"'{e['title']}'" for e in moved_events]
                    if len(event_names) == 1:
                        reply = f"Done! I've moved {event_names[0]} from {source_str} to {dest_str}."
                    else:
                        reply = f"Done! I've moved {len(moved_events)} events from {source_str} to {dest_str}: {', '.join(event_names)}."
                    metadata["events_updated"] = moved_events
                elif moved_events and conflict_events:
                    moved_names = [f"'{e['title']}'" for e in moved_events]
                    conflict_names = [f"'{c['event']['title']}'" for c in conflict_events]
                    reply = f"I've moved {len(moved_events)} events from {source_str} to {dest_str}: {', '.join(moved_names)}. However, {', '.join(conflict_names)} could not be moved due to conflicts."
                    metadata["events_updated"] = moved_events
                    metadata["conflicts"] = conflict_events
                else:
                    conflict_names = [f"'{c['event']['title']}'" for c in conflict_events]
                    reply = f"I couldn't move any events from {source_str} to {dest_str}. All events conflict with existing events on {dest_str}: {', '.join(conflict_names)}."
                    metadata["conflicts"] = conflict_events
    elif intent == "BULK_CANCEL":
        bulk_details = extract_bulk_operation_details(user_message)
        source_date = bulk_details["source_date"]

        if not source_date:
            reply = "I couldn't understand which date you want to cancel events on. Please specify the date."
        else:
            # Find all events on the source date
            events_to_cancel = database.query_events(username=username, start_date=source_date, end_date=source_date)

            if not events_to_cancel:
                try:
                    source_parsed = datetime.strptime(source_date, "%Y-%m-%d")
                    source_str = source_parsed.strftime("%B %d")
                except:
                    source_str = source_date
                reply = f"You don't have any events scheduled on {source_str}."
            else:
                deleted_events = []
                for event in events_to_cancel:
                    deleted = database.delete_event(event["event_id"])
                    if deleted:
                        deleted_events.append(deleted)

                # Build response
                try:
                    source_parsed = datetime.strptime(source_date, "%Y-%m-%d")
                    source_str = source_parsed.strftime("%B %d")
                except:
                    source_str = source_date

                event_names = [f"'{e['title']}'" for e in deleted_events]
                if len(event_names) == 1:
                    reply = f"Done! I've cancelled {event_names[0]} on {source_str}."
                else:
                    reply = f"Done! I've cancelled {len(deleted_events)} events on {source_str}: {', '.join(event_names)}."
                metadata["events_deleted"] = deleted_events
    elif intent == "UPDATE_RECURRING":
        # Update all events in a recurring series
        recurring_details = extract_recurring_operation_details(user_message)
        series_keyword = recurring_details.get("series_keyword")

        if not series_keyword:
            reply = "I couldn't understand which recurring series you want to update. Please specify the meeting name."
        else:
            update_participants = recurring_details.get("new_participants").copy()
            update_participants.append(username)
            result = update_recurring_series(
                username=username,
                series_keyword=series_keyword,
                new_title=recurring_details.get("new_title"),
                new_day=recurring_details.get("new_day"),
                new_time=recurring_details.get("new_time"),
                new_participants=update_participants
            )

            if result.get("error"):
                reply = result["error"]
            elif result["count"] == 0:
                # Not a recurring series — fall back to single-event update
                identifier = extract_event_identifier(user_message)
                events = database.query_events(
                    username=username,
                    start_date=identifier["current_date"],
                    end_date=identifier["current_date"],
                    participants=identifier["participants"],
                    keyword=identifier["keyword"]
                )
                if not events and identifier["keyword"]:
                    events = database.query_events(username=username, keyword=identifier["keyword"])
                if not events:
                    reply = f"No events found matching '{series_keyword}'."
                else:
                    event = events[0]
                    update_details = extract_update_details(user_message)
                    updates = {}
                    if update_details["new_title"] and update_details["new_title"] != event["title"]:
                        updates["title"] = update_details["new_title"]
                    if update_details["new_start_time"]:
                        updates["start_time"] = update_details["new_start_time"]
                        if not update_details["new_end_time"]:
                            time_delta = datetime.strptime(event["end_time"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
                            updates["end_time"] = (datetime.strptime(updates["start_time"], "%Y-%m-%d %H:%M:%S") + time_delta).strftime("%Y-%m-%d %H:%M:%S")
                    if update_details["new_end_time"]:
                        updates["end_time"] = update_details["new_end_time"]

                    if "start_time" in updates or "end_time" in updates:
                        new_start = updates.get("start_time", event["start_time"])
                        new_end = updates.get("end_time", event["end_time"])
                        conflicts = check_time_conflict(username, new_start, new_end, exclude_event_id=event["event_id"])
                        if conflicts:
                            conflict_msg = format_conflict_message(conflicts)
                            time_str = new_start
                            try:
                                parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                                time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
                            except:
                                pass
                            reply = f"I can't reschedule '{event['title']}' to {time_str}. {conflict_msg} Would you like to pick a different time?"
                            metadata["conflict"] = True
                            metadata["conflicting_events"] = conflicts
                        else:
                            updated = database.update_event(event["event_id"], **updates)
                            time_str = updates.get('start_time', '')
                            try:
                                parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                                time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
                            except:
                                pass
                            reply = f"Done! I've rescheduled '{event['title']}' to {time_str}."
                            metadata["events_updated"] = [updated]
                    elif updates:
                        updated = database.update_event(event["event_id"], **updates)
                        reply = f"Updated '{updated['title']}' successfully."
                        metadata["events_updated"] = [updated]
                    else:
                        reply = "I couldn't understand what you want to change."
            else:
                # Build a descriptive response
                change_parts = []
                if recurring_details.get("new_title"):
                    change_parts.append(f"renamed to '{recurring_details['new_title']}'")
                if recurring_details.get("new_day"):
                    change_parts.append(f"moved to {recurring_details['new_day'].capitalize()}s")
                if recurring_details.get("new_time"):
                    try:
                        time_parsed = datetime.strptime(recurring_details['new_time'], "%H:%M:%S")
                        time_formatted = time_parsed.strftime("%I:%M %p").lstrip("0")
                        change_parts.append(f"rescheduled to {time_formatted}")
                    except:
                        change_parts.append(f"rescheduled to {recurring_details['new_time']}")
                if recurring_details.get("new_participants"):
                    change_parts.append(f"updated participants to {', '.join(recurring_details.get("new_participants"))}")
                changes_str = " and ".join(change_parts) if change_parts else "updated"
                reply = f"Done! I've {changes_str} all {result['count']} events in the '{series_keyword}' series."
                metadata["events_updated"] = result["events"]
    elif intent == "DELETE_RECURRING":
        # Delete all events in a recurring series
        recurring_details = extract_recurring_operation_details(user_message)
        series_keyword = recurring_details.get("series_keyword")

        if not series_keyword:
            reply = "I couldn't understand which recurring series you want to delete. Please specify the meeting name."
        else:
            result = delete_recurring_series(username, series_keyword)

            if result.get("error"):
                reply = result["error"]
            elif result["count"] == 0:
                reply = f"No recurring events found matching '{series_keyword}'."
            else:
                reply = f"Done! I've deleted all {result['count']} events in the '{series_keyword}' series."
                metadata["events_deleted"] = result["deleted"]
    else:  # GENERAL or fallback
        # First, try to find relevant calendar events with notes
        # Extract filters to find specific events the user is asking about
        filters = extract_query_filters(user_message)

        # Search for events matching the query
        relevant_events = database.query_events(
            username=username,
            start_date=filters["start_date"],
            end_date=filters["end_date"],
            participants=filters["participants"],
            keyword=filters["keyword"]
        )

        # Build context from matching events with notes
        event_context = []
        for event in relevant_events:
            notes = event.get("notes", "")
            if notes and notes.strip():
                # Format date naturally (e.g., "January 29")
                try:
                    event_datetime = datetime.strptime(event['start_time'], "%Y-%m-%d %H:%M:%S")
                    date_str = event_datetime.strftime("%B %d").replace(" 0", " ").lstrip("0")
                except:
                    date_str = event['start_time'].split(' ')[0]
                event_context.append(f"Meeting '{event['title']}' on {date_str}: {notes}")

        # Also get RAG context
        query_vec = EMBED_MODEL.encode(user_message)
        top_docs = retrieve_top_k(username, query_vec, k=3)

        # Combine event notes context with RAG context
        all_context = event_context + top_docs
        context_text = "\n".join(all_context) if all_context else "No relevant context."

        reply = call_ollama(user_message, context_text)
        metadata["retrieved_docs"] = top_docs
        metadata["relevant_events"] = relevant_events

    # Save conversation to database
    database.save_conversation_message(username, user_message, reply)

    return reply, metadata

def classify_intent(user_message):
    """Use LLM to classify user intent semantically."""
    classification_prompt = f"""Classify the user's intent into exactly ONE of these categories:

- CREATE_RECURRING: User wants to create RECURRING/REPEATED events (every week, every day, weekly, daily, "for the next X weeks")
- CREATE: User wants to schedule a SINGLE one-time event/meeting/appointment
- DELETE: User wants to remove, cancel, or delete a SINGLE existing event
- DELETE_RECURRING: User wants to remove/cancel ALL events in a recurring series ("remove all my standups", "delete all Team Meetings", "cancel all my 1:1s")
- QUERY: User wants to see their SCHEDULE - list events, check availability, see what's on calendar (NOT asking about meeting content)
- UPDATE: User wants to change, modify, reschedule, or update a SINGLE existing event (time, title, participants)
- UPDATE_RECURRING: User wants to change ALL events in a recurring series ("rename all my Project Reviews to Budget Reviews", "move all my Morning Plannings to Tuesday", "change all standups to 10am")
- ADD_NOTES: User wants to add notes, comments, or a summary to an existing event (STATEMENTS like "We discussed X", "The meeting covered Y")
- BULK_RESCHEDULE: User wants to move/push/reschedule ALL events from one DATE to another DATE ("push everything today to tomorrow", "move all my meetings from Friday to Monday")
- BULK_CANCEL: User wants to cancel/delete ALL events on a specific DATE ("cancel everything today", "clear my calendar tomorrow")
- GENERAL: Questions about meeting CONTENT/DISCUSSIONS (like "What did we discuss?", "What was decided?", "How much was X increased?")

CRITICAL DISTINCTION - Recurring series vs Date-based:
- UPDATE_RECURRING/DELETE_RECURRING: Targets a recurring SERIES by name ("all my standups", "all Project Reviews")
- BULK_RESCHEDULE/BULK_CANCEL: Targets a specific DATE ("everything today", "everything tomorrow")

Important:
- "every Friday", "weekly", "every week", "daily", "for the next 4 weeks" = CREATE_RECURRING (not CREATE)
- "schedule a meeting tomorrow" = CREATE (single event, no recurrence)
- "Delete the standup" or "Cancel tomorrow's meeting" or "Cancel Product Testing" = DELETE (single event)
- "Remove ALL my standups" or "Delete all Team Meetings" = DELETE_RECURRING (recurring series)
- "Move my meeting to 3pm" or "Reschedule Product Meeting to Feb 10" = UPDATE (single event)
- "Reschedule my dinner with Charlie to tomorrow 8am" = UPDATE (single event, no "all")
- "Reschedule the Team Standup on Feb 11 to tomorrow" = UPDATE (targets ONE specific occurrence by date)
- "Change ALL my standups to 10am" or "Rename all Project Reviews" = UPDATE_RECURRING (recurring series, must say "all")
- "Push everything today to tomorrow" = BULK_RESCHEDULE (date-based)
- "Cancel everything today" = BULK_CANCEL (date-based)
- "Add notes to my meeting..." or "We discussed X" (STATEMENT) = ADD_NOTES intent
- "What did we discuss?" (QUESTION about past meetings) = GENERAL intent

KEY RULE: UPDATE_RECURRING and DELETE_RECURRING require the word "all" (e.g., "all my standups", "all Project Reviews"). BULK_RESCHEDULE and BULK_CANCEL require the word "everything" (e.g., "Push everything", "Cancel everything"). Without "all" or "everything", it is UPDATE or DELETE (single event). Also, if the user mentions a SPECIFIC DATE (like "on Feb 11", "tomorrow"), it is UPDATE or DELETE, NOT UPDATE_RECURRING or DELETE_RECURRING.

Message: {user_message}

Respond with ONLY the category name:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You classify user intents. Respond with only one word: CREATE_RECURRING, CREATE, DELETE, DELETE_RECURRING, QUERY, UPDATE, UPDATE_RECURRING, ADD_NOTES, BULK_RESCHEDULE, BULK_CANCEL, or GENERAL."},
            {"role": "user", "content": classification_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        response = res.json()["message"]["content"].strip().upper()

        # Extract just the intent keyword if there's extra text
        # Check longer intents first to avoid partial matches
        classified = "GENERAL"
        for intent in ["CREATE_RECURRING", "UPDATE_RECURRING", "DELETE_RECURRING", "BULK_RESCHEDULE", "BULK_CANCEL", "CREATE", "DELETE", "QUERY", "UPDATE", "ADD_NOTES", "GENERAL"]:
            if intent in response:
                classified = intent
                break

        # Guard: UPDATE_RECURRING/DELETE_RECURRING require "all" in the message
        msg_lower = user_message.lower()
        if classified == "UPDATE_RECURRING" and "all" not in msg_lower:
            classified = "UPDATE"
        elif classified == "DELETE_RECURRING" and "all" not in msg_lower:
            classified = "DELETE"

        return classified
    except Exception as e:
        return "GENERAL"
    
def extract_event_details(user_message):

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()

    # Prompt to feed
    extraction_prompt = f"""Extract event details from this message. Today is {today} (current time: {now.strftime("%H:%M")}).

Return ONLY valid JSON with these fields:
- title: string (the event name/description)
- start_time: string in "YYYY-MM-DD HH:MM:SS" format
- end_time: string in "YYYY-MM-DD HH:MM:SS" format (default to 45 min after start if not specified)
- participants: array of strings (names mentioned, empty array if none)

Interpret relative dates like "tomorrow", "next Monday", "this Friday" relative to today.
If no time specified, default to 09:00:00.
Examples:
- "Schedule a 1-hour Product Meeting tomorrow 4pm" -> title="Product Meeting", start_time=tomorrow 4pm, end_time=1 hour forward from start_time, participants=[]


Message: {user_message}

JSON:"""

    # LLM
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract event details and return only valid JSON. No explanations."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    now = datetime.now()

    try:
        # Asking LLM
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()

        # LLM's response
        response_text = res.json()["message"]["content"]

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        details = json.loads(response_text.strip())
        
        # Validate required fields
        if not all(k in details for k in ["title", "start_time", "end_time"]):
            raise ValueError("Missing required fields")

        # Normalize datetime values
        start_time = normalize_datetime(details["start_time"])
        end_time = normalize_datetime(details["end_time"])

        # If normalization failed, use defaults
        if not start_time:
            default_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if default_start < now:
                default_start += timedelta(days=1)
            start_time = default_start.strftime("%Y-%m-%d %H:%M:%S")

        if not end_time:
            try:
                start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                end_time = (start_dt + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
            except:
                end_time = start_time

        # Check for timezone in user message and convert to local (SGT)
        source_tz = extract_timezone_from_message(user_message)
        if source_tz:
            start_time = convert_to_local_tz(start_time, source_tz)
            end_time = convert_to_local_tz(end_time, source_tz)

        details["start_time"] = start_time
        details["end_time"] = end_time

        return details
    except Exception as e:
        # Fallback: use message as title with default time 9:00am
        default_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if default_start < now:
            default_start += timedelta(days=1)
        return {
            "title": user_message,
            "start_time": default_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": (default_start + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
            "participants": []
        }

def call_ollama(user_message, context=""):

    today = datetime.now().strftime("%Y-%m-%d")

    # Context
    context_section = f"Context from your calendar and meeting notes:\n{context}\n\n" if context else ""

    # Prompt
    context_prompt = f"""You are an AI calendar assistant. Today's date is {today}.

When answering questions:
- Use the provided context from calendar events and meeting notes to answer questions
- If the context contains relevant meeting notes, reference which meeting the information is from
- If asked about decisions, discussions, or action items, look for them in the meeting notes
- Be concise but informative
- If the context doesn't contain relevant information, say so honestly"""

    # LLM
    payload = {"model": MODEL_NAME,
               "messages": [
                    {
                        "role": "system",
                        "content": context_prompt
                    },
                    {
                        "role": "user",
                        "content": f"{context_section}User question: {user_message}"
                    }
                ],
                "stream": False}
    try:
        # Asking LLM
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()

        # LLM's response
        data = res.json()
        return data["message"]["content"]
    except Exception as e:
        return "Sorry, I couldn't process your request."
    
def store_event_embedding(event_id, content):
    """Store embedding for an event in the database."""
    vector = EMBED_MODEL.encode(content)
    database.update_event_embedding(event_id, vector)
    return vector
    
def retrieve_top_k(username, query_vector, k=3):
    """Retrieve top-k most similar embeddings from events and conversations."""
    results = []

    # Get events with embeddings
    events_with_embeddings = database.get_events_with_embeddings(username)
    for event in events_with_embeddings:
        if event["embedding"] is not None:
            similarity = util.cos_sim(query_vector, event["embedding"]).item()
            content = f"Meeting '{event['title']}' on {event['start_time'].split(' ')[0]}: {event['notes']}"
            results.append((content, similarity))

    # Get conversations with embeddings
    conversations_with_embeddings = database.get_conversations_with_embeddings(username)
    for conv in conversations_with_embeddings:
        if conv["embedding"] is not None:
            similarity = util.cos_sim(query_vector, conv["embedding"]).item()
            results.append((conv["content"], similarity))

    # Sort by similarity and return top k
    results.sort(key=lambda x: x[1], reverse=True)
    return [content for content, _ in results[:k]]

def normalize_datetime(datetime_str, original_message=None):
    """
    Normalize a datetime string to YYYY-MM-DD HH:MM:SS format.
    Handles relative dates like 'tomorrow', 'next week', etc.
    If original_message is provided, check it for day names to override extracted date.
    """
    if not datetime_str:
        return None

    datetime_str = datetime_str.strip()
    current = datetime.now()

    # Try to extract time portion if present
    time_match = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', datetime_str)
    time_str = time_match.group(1) if time_match else "09:00:00"
    if len(time_str) == 5:  # HH:MM format
        time_str += ":00"

    # Check the original message for day names (more reliable than LLM extraction)
    check_str = (original_message or datetime_str).lower()
    target_date = None

    # Day name map
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6
    }

    # Handle relative date keywords
    if "tomorrow" in check_str:
        target_date = current.date() + timedelta(days=1)
    elif "today" in check_str and "not today" not in check_str:
        target_date = current.date()
    elif "next week" in check_str:
        target_date = current.date() + timedelta(weeks=1)
    elif "next month" in check_str:
        target_date = current.date() + timedelta(days=30)
    else:
        # Check for day names
        for day_name, day_num in day_map.items():
            if day_name in check_str:
                days_ahead = day_num - current.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                target_date = current.date() + timedelta(days=days_ahead)
                break

    if target_date:
        return f"{target_date.strftime('%Y-%m-%d')} {time_str}"

    # If already in correct format and no day name found, return as-is
    try:
        datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
        return datetime_str
    except ValueError:
        pass

    # Couldn't normalize - return None to indicate failure
    return None

def extract_timezone_from_message(user_message):
    """Use LLM to detect if a timezone is mentioned in the user message."""
    # Pre-check: only call LLM if a known timezone keyword exists in the message
    tz_keywords = [
        "pst", "pdt", "est", "edt", "cst", "cdt", "mst", "mdt",
        "gmt", "utc", "bst", "cet", "ist", "jst", "sgt", "aest", "hkt", "kst",
        "pacific time", "eastern time", "central time", "mountain time",
        "singapore time", "tokyo time", "london time",
    ]
    msg_lower = user_message.lower()
    if not any(kw in msg_lower for kw in tz_keywords):
        return None

    extraction_prompt = f"""Does this message contain an EXPLICIT timezone abbreviation or name?

Rules:
- "am" and "pm" are NOT timezones. "7pm", "9am", "3:00pm" have NO timezone.
- "Sun", "Mon", "Tue" etc. are days of the week, NOT timezones.
- Only these count as timezones: PST, PDT, EST, EDT, CST, CDT, MST, MDT, GMT, UTC, BST, CET, IST, JST, SGT, AEST, HKT, KST, or full names like "Pacific time", "Eastern time", "Singapore time".

Examples:
- "Reschedule all standups to Sun 7pm" -> null
- "meeting at 6pm" -> null
- "Friday 6pm" -> null
- "meeting at 3pm PST" -> America/Los_Angeles
- "call at 9am Eastern" -> America/New_York
- "schedule for 2pm SGT" -> Asia/Singapore

Message: {user_message}

Return ONLY "null" or the IANA timezone name. Nothing else:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You detect timezones in messages. Return only the IANA timezone name or null."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        raw_response = res.json()["message"]["content"].strip()

        # Clean up response (keep case for ZoneInfo validation)
        response = raw_response.replace('"', '').replace("'", "").strip()
        response_lower = response.lower()

        if response_lower == "null" or response_lower == "none" or not response:
            return None

        # Validate it's a real timezone (try original case first)
        try:
            ZoneInfo(response)
            return response
        except:
            pass

        # Try common IANA formats with proper casing
        iana_formats = {
            "america/los_angeles": "America/Los_Angeles",
            "america/new_york": "America/New_York",
            "america/chicago": "America/Chicago",
            "america/denver": "America/Denver",
            "europe/london": "Europe/London",
            "europe/paris": "Europe/Paris",
            "asia/tokyo": "Asia/Tokyo",
            "asia/singapore": "Asia/Singapore",
            "asia/hong_kong": "Asia/Hong_Kong",
            "asia/kolkata": "Asia/Kolkata",
            "asia/shanghai": "Asia/Shanghai",
            "asia/seoul": "Asia/Seoul",
            "australia/sydney": "Australia/Sydney",
        }
        if response_lower in iana_formats:
            tz = iana_formats[response_lower]
            return tz

        # Try to match common abbreviations
        tz_mapping = {
            "pst": "America/Los_Angeles",
            "pdt": "America/Los_Angeles",
            "pacific": "America/Los_Angeles",
            "est": "America/New_York",
            "edt": "America/New_York",
            "eastern": "America/New_York",
            "cst": "America/Chicago",
            "cdt": "America/Chicago",
            "central": "America/Chicago",
            "mst": "America/Denver",
            "mdt": "America/Denver",
            "mountain": "America/Denver",
            "gmt": "UTC",
            "utc": "UTC",
            "bst": "Europe/London",
            "london": "Europe/London",
            "jst": "Asia/Tokyo",
            "tokyo": "Asia/Tokyo",
            "sgt": "Asia/Singapore",
            "singapore": "Asia/Singapore",
            "hkt": "Asia/Hong_Kong",
            "ist": "Asia/Kolkata",
            "aest": "Australia/Sydney",
        }
        for key, tz in tz_mapping.items():
            if key in response_lower:
                return tz
        return None
    except Exception as e:
        return None
    
def convert_to_local_tz(datetime_str, source_tz_name):
    """Convert a datetime string from source timezone to local timezone (SGT)."""
    if not datetime_str or not source_tz_name:
        return datetime_str

    try:
        # Parse the datetime
        dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")

        # Attach the source timezone
        source_tz = ZoneInfo(source_tz_name)
        dt_with_tz = dt.replace(tzinfo=source_tz)

        # Convert to local timezone
        dt_local = dt_with_tz.astimezone(ZoneInfo("Asia/Singapore"))

        result = dt_local.strftime("%Y-%m-%d %H:%M:%S")
        # Return as string without timezone info (for storage)
        return result
    except Exception as e:
        return datetime_str

def convert_time_to_local_tz(time_str, source_tz_name):
    """Convert a time string (HH:MM:SS) from source timezone to local timezone (SGT)."""
    if not time_str or not source_tz_name:
        return time_str

    try:
        # Create a datetime using today's date + the time
        today_date = datetime.now().strftime("%Y-%m-%d")
        full_datetime = f"{today_date} {time_str}"

        # Convert using the full datetime function
        converted = convert_to_local_tz(full_datetime, source_tz_name)
        offset = 0
        if datetime.strptime(converted, "%Y-%m-%d %H:%M:%S").date() < datetime.strptime(full_datetime, "%Y-%m-%d %H:%M:%S").date():
            offset = -1
        elif datetime.strptime(converted, "%Y-%m-%d %H:%M:%S").date() > datetime.strptime(full_datetime, "%Y-%m-%d %H:%M:%S").date():
            offset = 1
        # Extract just the time portion
        final_str = ""
        if converted:
            final_str = converted.split(" ")[1]
        else:
            final_str = time_str
        return final_str, offset
    except Exception as e:
        return time_str
    
def extract_query_filters(user_message):
    """Use LLM to extract query/filter criteria from natural language."""

    today = datetime.now().strftime("%Y-%m-%d")
    current_year = datetime.now().year
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # Calculate next week's Monday and Sunday
    now = datetime.now()
    days_until_next_monday = (7 - now.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    next_monday = (now + timedelta(days=days_until_next_monday)).strftime("%Y-%m-%d")
    next_sunday = (now + timedelta(days=days_until_next_monday + 6)).strftime("%Y-%m-%d")

    extraction_prompt = f"""Extract search filters from this message. Today is {today}, tomorrow is {tomorrow}.

Return ONLY valid JSON with these fields (use null if NOT EXPLICITLY specified):
- start_date: string in "YYYY-MM-DD" format or null (start of date range)
- end_date: string in "YYYY-MM-DD" format or null (end of date range)
- participants: array of strings (names mentioned) or null
- keyword: string (specific event title/topic to search for) or null

IMPORTANT:
- "today" = {today}
- "tomorrow" = {tomorrow}
- "next week" = start_date: "{next_monday}", end_date: "{next_sunday}"
- "Feb 2" or "February 2" = "{current_year}-02-02"
- If no date is mentioned, use null for both start_date and end_date
- "keyword" is ONLY for specific event names like "Project Review", "Standup", "Lunch with Bob"
- Generic words like "events", "meetings", "calendar", "schedule" are NOT keywords — use null

Examples:
- "Who is the Project Review tomorrow with?" -> {{"start_date": "{tomorrow}", "end_date": "{tomorrow}", "participants": null, "keyword": "Project Review"}}
- "Who was the Morning Planning with on Feb 2?" -> {{"start_date": "{current_year}-02-02", "end_date": "{current_year}-02-02", "participants": null, "keyword": "Morning Planning"}}
- "What's on my calendar today?" -> {{"start_date": "{today}", "end_date": "{today}", "participants": null, "keyword": null}}
- "what's on my calendar?" -> {{"start_date": null, "end_date": null, "participants": null, "keyword": null}}
- "What are my events next week?" -> {{"start_date": "{next_monday}", "end_date": "{next_sunday}", "participants": null, "keyword": null}}
- "list events with Alice" -> {{"start_date": null, "end_date": null, "participants": ["Alice"], "keyword": null}}

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract search filters and return only valid JSON. No explanations."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        filters = json.loads(response_text.strip())

        # Post-process dates to handle relative date strings the LLM might return
        start_date = filters.get("start_date")
        end_date = filters.get("end_date")

        if start_date:
            start_lower = start_date.lower().strip()
            if start_lower == "today" or "today" in start_lower:
                start_date = today
            elif start_lower == "tomorrow" or "tomorrow" in start_lower:
                start_date = tomorrow

        if end_date:
            end_lower = end_date.lower().strip()
            if end_lower == "today" or "today" in end_lower:
                end_date = today
            elif end_lower == "tomorrow" or "tomorrow" in end_lower:
                end_date = tomorrow

        return {
            "start_date": start_date,
            "end_date": end_date,
            "participants": filters.get("participants"),
            "keyword": filters.get("keyword")
        }
    except Exception as e:
        return {"start_date": None, "end_date": None, "participants": None, "keyword": None}
    
def extract_recurring_operation_details(user_message):
    """
    Extract details for recurring series operations (UPDATE_RECURRING, DELETE_RECURRING).

    These target a recurring SERIES by name, not a specific date.
    Examples:
    - "Change the title of all my Project Reviews to Budget Reviews"
    - "Reschedule all my Morning Plannings to every Tuesday 3pm"
    - "Remove all my Team Standups"
    """

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    current_weekday = now.strftime("%A")

    extraction_prompt = f"""Extract details for an operation on a RECURRING event series. Today is {today} ({current_weekday}).

The user wants to modify or delete ALL occurrences in a recurring series. Extract:

1. series_keyword: The name/title of the recurring series to target (e.g., "standups", "Project Review", "Morning Planning")
2. For updates, what changes to make:
   - new_title: New name for the series (null if not changing title)
   - new_day: New day of week (null if not changing day) - use lowercase: "monday", "tuesday", etc.
   - new_time: New time in HH:MM:SS format (null if not changing time)

Return ONLY valid JSON with these fields:
- series_keyword: string (the recurring series name to search for)
- new_title: string or null (new name if renaming)
- new_day: string or null (new day of week if rescheduling, lowercase)
- new_time: string in "HH:MM:SS" format or null (new time if rescheduling)
- new_participants: array of strings (names mentioned, empty array if none)
IMPORTANT: Use the EXACT event name from the user's message for series_keyword. Do NOT rename or map to other event names.

series_keyword examples:
- "all my standups" -> series_keyword: "standup"
- "all Project Reviews" -> series_keyword: "Project Review"
- "all Budget Reviews" -> series_keyword: "Budget Review"
- "all my Morning Plannings" -> series_keyword: "Morning Planning"
- "all 1:1s" or "all one-on-ones" -> series_keyword: "1:1"

TIME CONVERSION (use 24-hour format):
- AM times: "9am" -> "09:00:00", "10am" -> "10:00:00", "11am" -> "11:00:00"
- PM times: Add 12 to the hour! "1pm" -> "13:00:00", "2pm" -> "14:00:00", "3pm" -> "15:00:00", "4pm" -> "16:00:00", "5pm" -> "17:00:00", "6pm" -> "18:00:00", "7pm" -> "19:00:00"
- Special: "12pm" (noon) -> "12:00:00", "12am" (midnight) -> "00:00:00"

Examples:
- "Change the title of all my Project Reviews to Budget Reviews" -> {{"series_keyword": "Project Review", "new_title": "Budget Reviews", "new_day": null, "new_time": null, "new_participants": []}}
- "Reschedule all my Morning Plannings to every Tuesday 3pm" -> {{"series_keyword": "Morning Planning", "new_title": null, "new_day": "tuesday", "new_time": "15:00:00", "new_participants": []}}
- "Move all standups to 10am" -> {{"series_keyword": "standup", "new_title": null, "new_day": null, "new_time": "10:00:00", "new_participants": []}}
- "Reschedule all 1:1s to Friday 6pm" -> {{"series_keyword": "1:1", "new_title": null, "new_day": "friday", "new_time": "18:00:00", "new_participants": []}}
- "Remove all my Team Standups" -> {{"series_keyword": "Team Standup", "new_title": null, "new_day": null, "new_time": null, "new_participants": []}}
- "Delete all 1:1s with Manager" -> {{"series_keyword": "1:1", "new_title": null, "new_day": null, "new_time": null, "new_participants": []}}
- "Change the participants of all my Team Standups to Bob and Charlie" -> {{"series_keyword": "Team Standup", "new_title": null, "new_day": null, "new_time": null, "new_participants": ["Bob", "Charlie"]}}

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract recurring series operation details. Return only valid JSON. CRITICAL: For PM times, ADD 12 to the hour (1pm=13:00, 2pm=14:00, 3pm=15:00, 4pm=16:00, 5pm=17:00, 6pm=18:00, 7pm=19:00, 8pm=20:00)."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        details = json.loads(response_text.strip())

        day_of_event = details.get("new_day")
        # Handle timezone conversion for new_time if present
        new_time = details.get("new_time")
        if new_time:
            source_tz = extract_timezone_from_message(user_message)
            if source_tz:
                days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                new_time, offset = convert_time_to_local_tz(new_time, source_tz)
                if offset == -1 and details.get("new_day") == "monday":
                    day_of_event = "sunday"
                elif offset == 1 and details.get("new_day") == "sunday":
                    day_of_event = "monday"
                else:
                    day_of_event = days_of_week[days_of_week.index(details.get("new_day")) + offset]

        return {
            "series_keyword": details.get("series_keyword"),
            "new_title": details.get("new_title"),
            "new_day": day_of_event,
            "new_time": new_time,
            "new_participants": details.get("new_participants")
        }
    except Exception as e:
        return {
            "series_keyword": None,
            "new_title": None,
            "new_day": None,
            "new_time": None,
            "new_participants": []
        }

def extract_event_identifier(user_message):
    """Use LLM to identify WHICH event the user wants to update (not the new values)."""

    today = datetime.now().strftime("%Y-%m-%d")
    extraction_prompt = f"""Identify which EXISTING event the user is referring to. Today is {today}.

The user wants to UPDATE an event. Extract identifiers for the CURRENT event (NOT the new values):
- keyword: the event type/name (e.g., "meeting", "standup", "lunch")
- participants: people currently in the event (NOT people being added)
- current_date: the CURRENT date of the event, ONLY if explicitly stated (e.g., "my 2pm meeting" or "tomorrow's standup")

IMPORTANT: Distinguish between CURRENT event info vs NEW values:
- "reschedule my meeting to 3pm tomorrow" -> keyword="meeting", current_date=null (tomorrow is the NEW time)
- "move tomorrow's standup to Friday" -> keyword="standup", current_date=tomorrow (tomorrow is CURRENT, Friday is NEW)
- "change my 2pm meeting to 4pm" -> keyword="meeting", current_date=null (no current DATE specified, just time)
- "change my product meeting to 9 feb 5pm" -> keyword="product meeting", current_date=null (9 feb 5pm is NEW)
- "rename the team meeting to Sprint Review" -> keyword="team meeting", current_date=null
- "Reschedule the Product Meeting to 11 Feb 8am." -> keyword="Product Meeting", current_date=null (11 Feb 8am is NEW, NOT current_date)

Return ONLY valid JSON:
- keyword: string or null (event type/title to search for)
- participants: array of strings or null (current participants)
- current_date: string in "YYYY-MM-DD" format or null (current event date, ONLY if explicitly stated)

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You identify which event the user is referring to. Return only valid JSON. No explanations."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        identifier = json.loads(response_text.strip())
        return {
            "keyword": identifier.get("keyword"),
            "participants": identifier.get("participants"),
            "current_date": identifier.get("current_date")
        }
    except Exception as e:
        return {"keyword": None, "participants": None, "current_date": None}
    
def extract_update_details(user_message):
    """Use LLM to extract what changes the user wants to make to an event."""

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()

    extraction_prompt = f"""Extract the UPDATE details from this message. Today is {today} (current time: {now.strftime("%H:%M")}).

The user wants to modify an existing event. Extract ONLY the NEW values they want to CHANGE TO (use null for fields not being changed).

CRITICAL: When the message has TWO dates (e.g., "on 11 Feb to 13 Feb 8pm"), the date BEFORE "to" identifies WHICH event, the date AFTER "to" is the NEW date/time. Only return the NEW date/time.

Return ONLY valid JSON with these fields:
- new_title: string or null (new event name if changing)
- new_start_time: string in "YYYY-MM-DD HH:MM:SS" format or null (new start time if rescheduling)
- new_end_time: string in "YYYY-MM-DD HH:MM:SS" format or null (new end time if changing duration)
- new_participants: array of strings or null (replacement participants list if changing)
- add_participants: array of strings or null (participants to ADD to existing list)
- remove_participants: array of strings or null (participants to REMOVE from existing list)

Interpret relative dates like "tomorrow", "next Monday" relative to today.

Examples:
- "reschedule my meeting to 3pm tomorrow" -> {{"new_start_time": "tomorrow 15:00:00", ...rest null}}
- "change the team standup to 10am" -> {{"new_start_time": "... 10:00:00", ...rest null}}
- "rename my meeting to Project Review" -> {{"new_title": "Project Review", ...rest null}}
- "add Bob to the meeting" -> {{"add_participants": ["Bob"], ...rest null}}
- "move my 2pm meeting to Friday at 4pm" -> {{"new_start_time": "Friday 16:00:00", ...rest null}}
- "Reschedule the Team Standup on 11 Feb to 13 Feb 8pm" -> {{"new_start_time": "2026-02-13 20:00:00", ...rest null}} (13 Feb is the NEW date, 11 Feb is ignored)

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract event update details and return only valid JSON. No explanations."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        details = json.loads(response_text.strip())

        # Normalize datetime values to ensure proper format
        # Pass the original user_message so day names like "Saturday" can be detected
        new_start = details.get("new_start_time")
        new_end = details.get("new_end_time")

        if new_start:
            new_start = normalize_datetime(new_start, user_message)
        if new_end:
            new_end = normalize_datetime(new_end, user_message)

        # Check for timezone in user message and convert to local (SGT)
        source_tz = extract_timezone_from_message(user_message)
        if source_tz and new_start:
            new_start = convert_to_local_tz(new_start, source_tz)
            if new_end:
                new_end = convert_to_local_tz(new_end, source_tz)

        return {
            "new_title": details.get("new_title"),
            "new_start_time": new_start,
            "new_end_time": new_end,
            "new_participants": details.get("new_participants"),
            "add_participants": details.get("add_participants"),
            "remove_participants": details.get("remove_participants")
        }
    except Exception as e:
        return {
            "new_title": None, "new_start_time": None, "new_end_time": None,
            "new_participants": None, "add_participants": None, "remove_participants": None
        }

def extract_recurring_details(user_message):
    """Use LLM to extract recurring event details from natural language."""

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    current_weekday = now.strftime("%A")

    extraction_prompt = f"""Extract recurring event details from this message. Today is {today} ({current_weekday}).

The user wants to create RECURRING events. Extract:
1. Event details (title, time, participants)
2. Recurrence pattern (which day, frequency)
3. Limit (how many occurrences or end date)

Return ONLY valid JSON with these fields:
- title: string (the event name/description)
- time: string in "HH:MM:SS" format (the time of day for the event)
- duration_minutes: integer (duration in minutes, default 45)
- participants: array of strings (names mentioned, empty array if none)
- frequency: string - "weekly" or "daily" (default "weekly")
- day_of_week: string or null (for weekly: "monday", "tuesday", etc. - extract from message or use today's day)
- occurrence_limit: integer or null (number of events to create, e.g., "3 meetings" = 3)
- end_date: string in "YYYY-MM-DD" format or null (e.g., "till March" or "this month")

IMPORTANT:
- If no limit specified, use occurrence_limit: 4 (default)
- "every Friday at 5pm" -> day_of_week: "friday", time: "17:00:00"
- "daily standup at 9am" -> frequency: "daily", time: "09:00:00"
- "for the next 3 weeks" -> occurrence_limit: 3
- For end_date, ALWAYS use YYYY-MM-DD format or use these EXACT keywords: "end_of_month", "end_of_year"
- "till March" or "until March" -> end_date: "2026-03-01" (first day of that month)
- "this month" or "end of month" -> end_date: "end_of_month" (special keyword)

Examples:
- "Set a progress meeting for every friday 5pm" -> {{"title": "Progress Meeting", "time": "17:00:00", "duration_minutes": 45, "participants": [], "frequency": "weekly", "day_of_week": "friday", "occurrence_limit": 4, "end_date": null}}
- "2-hour Weekly standup with Bob and Charlie every Monday 9am for 3 weeks" -> {{"title": "Weekly Standup", "time": "09:00:00", "duration_minutes": 120, "participants": ["Bob", "Charlie"], "frequency": "weekly", "day_of_week": "monday", "occurrence_limit": 3, "end_date": null}}
- "1-hour Daily check-in at 10am till end of month" -> {{"title": "Daily Check-in", "time": "10:00:00", "duration_minutes": 60, "participants": [], "frequency": "daily", "day_of_week": null, "occurrence_limit": null, "end_date": "end_of_month"}}

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract recurring event details and return only valid JSON. No explanations."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        details = json.loads(response_text.strip())

        # Get time value
        time_value = details.get("time") or "09:00:00"
        day_of_event = details.get("day_of_week")
        # Check for timezone in user message and convert to local (SGT)
        source_tz = extract_timezone_from_message(user_message)
        if source_tz:
            days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            time_value, offset = convert_time_to_local_tz(time_value, source_tz)
            if offset == -1 and details.get("day_of_week") == "monday":
                day_of_event = "sunday"
            elif offset == 1 and details.get("day_of_week") == "sunday":
                day_of_event = "monday"
            else:
                day_of_event = days_of_week[days_of_week.index(details.get("day_of_week")) + offset]
                
        # Use 'or' to handle None values from LLM returning null
        return {
            "title": details.get("title") or "Recurring Event",
            "time": time_value,
            "duration_minutes": details.get("duration_minutes") or 45,
            "participants": details.get("participants") or [],
            "frequency": details.get("frequency") or "weekly",
            "day_of_week": day_of_event,
            "occurrence_limit": details.get("occurrence_limit") or 4,  # Default to 4 if null/None
            "end_date": details.get("end_date")
        }
    except Exception as e:
        return {
            "title": "Recurring Event",
            "time": "09:00:00",
            "duration_minutes": 45,
            "participants": [],
            "frequency": "weekly",
            "day_of_week": None,
            "occurrence_limit": 4,
            "end_date": None
        }
    
def extract_bulk_operation_details(user_message):
    """Extract source date and destination date for bulk reschedule/cancel operations."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    current_weekday = now.strftime("%A") 

    extraction_prompt = f"""Extract the dates from this bulk calendar operation. Today is {today} ({current_weekday}).

The user wants to move/reschedule/cancel ALL events from one date. Extract:
- source_date: The date to move events FROM (or cancel events on)
- destination_date: The date to move events TO (null if canceling)

Return ONLY valid JSON with these fields:
- source_date: string in "YYYY-MM-DD" format (the date events are being moved FROM)
- destination_date: string in "YYYY-MM-DD" format or null (the date events are being moved TO, null if canceling)

IMPORTANT:
- "today" = {today}
- "tomorrow" = the day after today
- "Push everything today to tomorrow" -> source_date: today, destination_date: tomorrow
- "Cancel everything on Friday" -> source_date: next Friday, destination_date: null
- "Move all meetings from Feb 10 to Feb 12" -> source_date: "2026-02-10", destination_date: "2026-02-12"

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract dates for bulk calendar operations. Return only valid JSON."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        details = json.loads(response_text.strip())

        # Handle relative dates
        source_date = details.get("source_date")
        destination_date = details.get("destination_date")

        if source_date:
            source_lower = source_date.lower().strip()
            if source_lower == "today":
                source_date = today
            elif source_lower == "tomorrow":
                source_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        if destination_date:
            dest_lower = destination_date.lower().strip()
            if dest_lower == "today":
                destination_date = today
            elif dest_lower == "tomorrow":
                destination_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        return {
            "source_date": source_date,
            "destination_date": destination_date
        }
    except Exception as e:
        return {
            "source_date": today,
            "destination_date": None
        }
    
def extract_notes_details(user_message):
    """Use LLM to extract which event to add notes to and what the notes are."""

    today = datetime.now().strftime("%Y-%m-%d")

    extraction_prompt = f"""Extract note details from this message. Today is {today}.

The user wants to ADD NOTES to an existing event. Extract:
1. Which event they're referring to (keyword, participants, date)
2. The actual notes/content to add

Return ONLY valid JSON with these fields:
- keyword: string or null (event type/title to search for, e.g., "meeting", "standup")
- participants: array of strings or null (people in the event)
- event_date: string in "YYYY-MM-DD" format or null (when the event was/is)
- notes: string (the actual notes content to add - extract the meaningful content)

Examples:
- "Add notes to my meeting with Bob yesterday: we discussed the Q1 budget" -> {{"keyword": "meeting", "participants": ["Bob"], "event_date": "yesterday's date", "notes": "Discussed the Q1 budget"}}
- "The standup this morning covered sprint progress" -> {{"keyword": "standup", "participants": null, "event_date": "{today}", "notes": "Covered sprint progress"}}
- "Notes for yesterday's team meeting: action items - finish design doc, review PRs" -> {{"keyword": "team meeting", "participants": null, "event_date": "yesterday's date", "notes": "Action items: finish design doc, review PRs"}}
- "Add Notes to Product meeting on 6 Feb: look into Bob's issue with authentication" -> {{"keyword": "Product meeting", "participants": null, "event_date": "2026-02-06", "notes": "Look into Bob's issue with authentication"}}

Message: {user_message}

JSON:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You extract event notes details and return only valid JSON. No explanations."},
            {"role": "user", "content": extraction_prompt}
        ],
        "stream": False
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=60)
        res.raise_for_status()
        response_text = res.json()["message"]["content"]

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
        if json_match:
            response_text = json_match.group(1)

        details = json.loads(response_text.strip())

        # Normalize event_date to handle relative dates like "today", "yesterday"
        event_date = details.get("event_date")
        if event_date:
            event_date_lower = event_date.lower().strip()
            if event_date_lower == "today" or "today" in event_date_lower:
                event_date = datetime.now().strftime("%Y-%m-%d")
            elif event_date_lower == "yesterday" or "yesterday" in event_date_lower:
                event_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            elif event_date_lower == "this morning" or "this morning" in event_date_lower:
                event_date = datetime.now().strftime("%Y-%m-%d")
            # Keep as-is if already in YYYY-MM-DD format or other format

        return {
            "keyword": details.get("keyword"),
            "participants": details.get("participants"),
            "event_date": event_date,
            "notes": details.get("notes", "")
        }
    except Exception as e:
        return {"keyword": None, "participants": None, "event_date": None, "notes": ""}

def update_recurring_series(username, series_keyword, new_title=None, new_day=None, new_time=None, new_participants=[]):
    """
    Update all events in a recurring series.

    Parameters:
    - username: The user who owns the events
    - series_keyword: Name of the series to find
    - new_title: New title for all events (optional)
    - new_day: New day of week, e.g., "tuesday" (optional)
    - new_time: New time in HH:MM:SS format (optional)

    Returns dict with count of updated events and list of updated events.
    """

    now = datetime.now()
    current_weekday = now.strftime("%A")

    events = find_recurring_series_events(username, series_keyword)

    if not events:
        return {"count": 0, "events": [], "error": f"No recurring series found matching '{series_keyword}'"}

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6
    }

    updated_events = []

    for event in events:
        updates = {}

        # Update title if specified
        if new_title:
            updates["title"] = new_title

        # Update time/day if specified
        if new_day or new_time:
            try:
                current_start = datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
                current_end = datetime.strptime(event["end_time"], "%Y-%m-%d %H:%M:%S")
                duration = current_end - current_start

                # If changing day of week
                if new_day:
                    target_day = day_map.get(new_day.lower())
                    if target_day is not None:
                        current_day = current_start.weekday()
                        days_diff = target_day - current_day
                        current_start = current_start + timedelta(days=days_diff)
                        if day_map.get(current_weekday.lower()) > target_day and current_day > day_map.get(current_weekday.lower()):
                            current_start = current_start + timedelta(days=7)
                        if day_map.get(current_weekday.lower()) == target_day and new_time:
                            if datetime.now().time() > datetime.strptime(new_time, "%H:%M:%S"):
                                if target_day < current_day:
                                    current_start = current_start + timedelta(days=7)
                                
                # If changing time
                if new_time:
                    time_parts = new_time.split(":")
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                    second = int(time_parts[2]) if len(time_parts) > 2 else 0
                    current_start = current_start.replace(hour=hour, minute=minute, second=second)

                # Calculate new end time preserving duration
                new_end = current_start + duration

                updates["start_time"] = current_start.strftime("%Y-%m-%d %H:%M:%S")
                updates["end_time"] = new_end.strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                continue
        if new_participants:
            updates["participants"] = new_participants
        if updates:
            event_id = event.get("event_id")
            updated_event = database.update_event(event_id, **updates)
            if updated_event:
                updated_events.append(updated_event)

    return {
        "count": len(updated_events),
        "events": updated_events,
        "series_keyword": series_keyword
    }


def delete_recurring_series(username, series_keyword):
    """
    Delete all events in a recurring series.

    Parameters:
    - username: The user who owns the events
    - series_keyword: Name of the series to find and delete

    Returns dict with count of deleted events.
    """
    events = find_recurring_series_events(username, series_keyword)

    if not events:
        return {"count": 0, "deleted": [], "error": f"No recurring series found matching '{series_keyword}'"}

    deleted_events = []

    for event in events:
        event_id = event.get("event_id")
        try:
            deleted = database.delete_event(event_id)
            if deleted:
                deleted_events.append(deleted)
        except Exception as e:
            print(f"Error deleting event {event_id}: {e}")

    return {
        "count": len(deleted_events),
        "deleted": deleted_events,
        "series_keyword": series_keyword
    }

def find_recurring_series_events(username, series_keyword):
    """
    Find all events that belong to a recurring series matching the keyword.
    Returns list of events that share the same recurrence_group.
    """
    if not series_keyword:
        return []

    keyword_lower = series_keyword.lower().strip()
    all_events = database.get_user_events(username)

    # First, find events that match the keyword
    matching_group = ""

    for event in all_events:
        title_lower = event.get("title", "").lower().strip()
        recurrence_group = event.get("recurrence_group")

        # Check if title matches the keyword
        if keyword_lower in title_lower or title_lower in keyword_lower:
            if recurrence_group:
                matching_group = recurrence_group
                break

    # Get only current/future events in those recurrence groups
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []
    for event in all_events:
        if event.get("recurrence_group") == matching_group:
            if event.get("end_time", "") >= now_str:
                results.append(event)

    # Sort by start_time
    results.sort(key=lambda e: e.get("start_time", ""))

    return results

def calculate_recurring_dates(details):
    """Calculate the dates for recurring events based on the extracted details."""
    dates = []

    frequency = details.get("frequency") or "weekly"
    day_of_week = details.get("day_of_week")
    occurrence_limit = details.get("occurrence_limit") or 4  # Default to 4 events
    end_date_str = details.get("end_date")

    # Parse end_date if provided
    end_date = None
    if end_date_str:
        end_date_lower = end_date_str.lower().strip()
        # Handle special keywords
        if "end_of_month" in end_date_lower or "end of month" in end_date_lower or "this month" in end_date_lower:
            # Calculate last day of current month
            current = datetime.now()
            if current.month == 12:
                end_date = current.replace(year=current.year + 1, month=1, day=1).date() - timedelta(days=1)
            else:
                end_date = current.replace(month=current.month + 1, day=1).date() - timedelta(days=1)
        elif "end_of_year" in end_date_lower or "end of year" in end_date_lower:
            # Calculate last day of current year
            end_date = datetime(datetime.now().year, 12, 31).date()
        else:
            # Try to parse as YYYY-MM-DD
            try:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except:
                # Try other common formats
                for fmt in ["%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y"]:
                    try:
                        end_date = datetime.strptime(end_date_str, fmt).date()
                        break
                    except:
                        pass

    # Map day names to weekday numbers (0=Monday, 6=Sunday)
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6
    }

    current_date = datetime.now().date()

    if frequency == "weekly" and day_of_week:
        # Find the target weekday
        target_day = day_map.get(day_of_week.lower(), current_date.weekday())

        # Find the next occurrence of that day
        days_ahead = target_day - current_date.weekday()
        if days_ahead <= -1:  # Target day already happened this week
            days_ahead += 7
        elif days_ahead == 0 and datetime.now().time() > datetime.strptime(details.get("time"), '%H:%M:%S').time():
            days_ahead += 7
        next_date = current_date + timedelta(days=days_ahead)

        # Generate dates
        count = 0
        max_iterations = occurrence_limit  # Already defaults to 4

        while count < max_iterations:
            if end_date and next_date > end_date:
                break
            dates.append(next_date)
            count += 1
            next_date += timedelta(weeks=1)

    elif frequency == "daily":
        # Start from tomorrow for daily events
        next_date = current_date + timedelta(days=1)

        count = 0
        max_iterations = occurrence_limit  # Already defaults to 4

        while count < max_iterations:
            if end_date and next_date > end_date:
                break
            dates.append(next_date)
            count += 1
            next_date += timedelta(days=1)
    else:
        # Default: weekly starting from today's day of week, next week
        next_date = current_date + timedelta(weeks=1)
        count = 0
        max_iterations = occurrence_limit  # Already defaults to 4

        while count < max_iterations:
            if end_date and next_date > end_date:
                break
            dates.append(next_date)
            count += 1
            next_date += timedelta(weeks=1)

    return dates

# -----------------------------
# Conflict detection
# -----------------------------
def check_time_conflict(username, start_time, end_time, exclude_event_id=None):
    """
    Check if a new event would conflict with existing events.
    Returns a list of conflicting events, or empty list if no conflicts.

    A conflict occurs when:
    - New event starts during an existing event
    - New event ends during an existing event
    - New event completely contains an existing event
    """
    conflicts = []

    try:
        new_start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        new_end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return []  # Can't check conflicts with invalid times

    all_events = database.get_user_events(username)
    for event in all_events:
        # Skip the event we're updating (if any)
        if exclude_event_id is not None and event.get("event_id") == exclude_event_id:
            continue

        try:
            existing_start = datetime.strptime(event["start_time"], "%Y-%m-%d %H:%M:%S")
            existing_end = datetime.strptime(event["end_time"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue

        # Check for overlap
        # Two events overlap if one starts before the other ends AND ends after the other starts
        if new_start < existing_end and new_end > existing_start:
            conflicts.append(event)

    return conflicts


def format_conflict_message(conflicts):
    """Format a user-friendly message about conflicting events."""
    if len(conflicts) == 1:
        event = conflicts[0]
        time_str = event["start_time"]
        try:
            parsed = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            time_str = parsed.strftime("%B %d at %I:%M %p").replace(" 0", " ").lstrip("0")
        except:
            pass
        return f"This conflicts with '{event['title']}' scheduled for {time_str}."
    else:
        event_names = [f"'{e['title']}'" for e in conflicts[:2]]
        if len(conflicts) > 2:
            return f"This conflicts with {', '.join(event_names)} and {len(conflicts) - 2} other event(s)."
        return f"This conflicts with {' and '.join(event_names)}."