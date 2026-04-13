from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from pydantic import BaseModel
import datetime

from routes.users import router as userRouter
from routes.events import router as eventRouter
from routes.agendaSuggestions import router as agendaRouter
from routes.schedulingInsights import router as insightRouter
from routes.chat import router as chatRouter
from routes.chat import store_event_embedding
import database

def embed_existing_event_notes(username):
    """
    Embed notes from all existing calendar events into RAG.
    Called on startup to make pre-populated event notes searchable.
    """
    embedded_count = 0
    all_events = database.get_user_events(username)
    for event in all_events:
        notes = event.get("notes", "")
        if notes and notes.strip():
            # Format date naturally (e.g., "January 29")
            try:
                event_dt = datetime.strptime(event['start_time'], "%Y-%m-%d %H:%M:%S")
                date_str = event_dt.strftime("%B %d").replace(" 0", " ").lstrip("0")
            except:
                date_str = event['start_time'].split(' ')[0]
            # Embed the event notes with context
            content = f"Event '{event['title']}' on {date_str}: {notes}"
            store_event_embedding(event["event_id"], content)
            embedded_count += 1

    return embedded_count


@asynccontextmanager
async def lifespan(app):
    """Embed event notes for pre-populated test users on startup."""
    embed_existing_event_notes("Alice")
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React Vite default
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(userRouter)
app.include_router(eventRouter)
app.include_router(agendaRouter)
app.include_router(insightRouter)
app.include_router(chatRouter)
