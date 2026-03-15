import anthropic
import base64
import os
import json
import re

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL  = "claude-sonnet-4-5"


def extract_hashtags(text: str) -> str:
    """Return comma-separated tags from #hashtags found in text."""
    tags = re.findall(r'#(\w+)', text)
    return ",".join(dict.fromkeys(t.lower() for t in tags))


def _parse_json(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])
    return json.loads(raw)


def refine_note(raw_text: str, project_name: str) -> str:
    """Clean and refine a raw note. Returns the refined string."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=(
            "You are a writing assistant embedded in a productivity bot. "
            "Your job is to refine a rough note written by the user. "
            "Rules:\n"
            "- Fix grammar and spelling\n"
            "- Improve clarity and conciseness\n"
            "- Preserve all details and intent exactly\n"
            "- Do NOT summarize or remove any specifics\n"
            "- Keep hashtags (#tag) in place\n"
            "- Return ONLY the refined note text, no explanation, no quotes."
        ),
        messages=[{
            "role": "user",
            "content": f"Project: {project_name}\nNote: {raw_text}"
        }]
    )
    return response.content[0].text.strip()


def notes_to_tasks(notes: list, project_name: str) -> list:
    """Convert a list of note dicts into suggested tasks.

    Each note dict must have keys: id, refined_text, tags.
    Returns list of {title, description, tags, source_note_id, deadline}.
    """
    notes_text = "\n".join(
        [f"[note_id={n['id']}] {n['refined_text']}" for n in notes]
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": f"""You are a productivity assistant.
Convert these notes from the '{project_name}' project into actionable tasks.

Notes:
{notes_text}

Return ONLY a JSON array. Each item:
{{
  "title": "short task title (max 60 chars)",
  "description": "extra detail or null",
  "tags": "comma-separated tags or empty string",
  "source_note_id": <integer note_id or null>,
  "deadline": "YYYY-MM-DD if a due date is mentioned, otherwise null"
}}

Return ONLY the JSON array. No other text."""}]
    )

    return _parse_json(response.content[0].text)


def raw_input_to_tasks(raw_text: str, project_name: str) -> list:
    """Convert free-form task input into a list of structured task dicts.

    Handles both single tasks and numbered lists (e.g. "1. X  2. Y").
    Returns list of {title, description, tags, deadline}.
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": f"""You are a productivity assistant.
Convert this rough input into one or more structured tasks for the '{project_name}' project.

Input: {raw_text}

Rules:
- If the input contains numbered items (e.g. "1. ...", "2. ..."), create a separate task for each item.
- Otherwise create a single task.
- Extract any #hashtags into the tags field.

Return ONLY a JSON array. Each item:
{{
  "title": "short task title (max 60 chars)",
  "description": "extra detail or null",
  "tags": "comma-separated tags or empty string",
  "deadline": "YYYY-MM-DD if a due date is mentioned, otherwise null"
}}

Return ONLY the JSON array. No other text."""}]
    )

    return _parse_json(response.content[0].text)


# ── New AI functions ───────────────────────────────────────────────────────────

def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe audio using OpenAI Whisper. Raises if OPENAI_API_KEY not set."""
    import openai
    import io
    oai = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    buf = io.BytesIO(audio_bytes)
    buf.name = filename
    result = oai.audio.transcriptions.create(model="whisper-1", file=buf)
    return result.text.strip()


def extract_text_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """Extract all readable text from an image using Claude vision."""
    img_b64 = base64.b64encode(image_bytes).decode()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": img_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "Extract all readable text from this image. "
                        "Preserve the structure and formatting as much as possible. "
                        "If it's a whiteboard, handwritten note, or document, extract all text verbatim. "
                        "Return ONLY the extracted text, no explanation."
                    ),
                },
            ],
        }]
    )
    return response.content[0].text.strip()


def classify_content(text: str, project_name: str) -> dict:
    """Classify text into an action type and extract structured fields.

    Returns a dict with keys:
      action       : save_note | save_task | save_idea | save_journal
      content      : cleaned text
      title        : short title (for tasks), else null
      tags         : comma-separated hashtags or empty string
      deadline     : YYYY-MM-DD or null
      calendar_event: {title, date (YYYYMMDD), time (HHMM or null)} or null
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": f"""You are a productivity assistant that classifies input.
Project: {project_name}
Input: {text}

Classify the input and return ONLY a JSON object:
{{
  "action": "save_note" | "save_task" | "save_idea" | "save_journal",
  "content": "cleaned version of the input",
  "title": "short task title (max 60 chars) if action is save_task, otherwise null",
  "tags": "comma-separated #hashtags or empty string",
  "deadline": "YYYY-MM-DD if a specific date is mentioned, otherwise null",
  "calendar_event": {{"title": "event title", "date": "YYYYMMDD", "time": "HHMM or null"}} or null
}}

Classification rules:
- save_task   : actionable item, to-do, something with a next step or deadline
- save_note   : factual info, observation, something to remember
- save_idea   : creative/speculative concept, brainstorming, "what if", future thinking
- save_journal: personal reflection, how you feel, what happened today, diary-style
- Include calendar_event only when action is save_task or save_note AND a specific date is mentioned.

Return ONLY the JSON object. No other text."""}]
    )
    return _parse_json(response.content[0].text)


def generate_daily_digest(activity: dict) -> str:
    """Generate a friendly 5-sentence daily digest from an activity summary dict."""
    upcoming = ", ".join(
        f"{t['title']} (due {t['deadline']})"
        for t in activity.get("upcoming_tasks", [])
    ) or "none"
    projects = ", ".join(activity.get("project_names", [])) or "various projects"

    summary = (
        f"Notes captured: {activity['notes']}, "
        f"Tasks created: {activity['tasks_created']}, "
        f"Tasks completed: {activity['tasks_completed']}, "
        f"Ideas saved: {activity['ideas']}, "
        f"Journal entries: {activity['journal']}, "
        f"References saved: {activity['references']}. "
        f"Active projects: {projects}. "
        f"Upcoming tasks with deadlines: {upcoming}."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": f"""Write a friendly 5-sentence daily digest for a productivity app user.

Today's activity:
{summary}

Rules:
- Be warm and encouraging
- Highlight what they accomplished
- Mention upcoming deadlines if any
- Suggest what to focus on next if appropriate
- Exactly 5 sentences, plain text (no markdown)

Write the digest now:"""}]
    )
    return response.content[0].text.strip()
