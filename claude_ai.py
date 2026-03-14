import anthropic
import os
import json

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are an AI assistant embedded in a Telegram productivity bot.
You help users manage notes and tasks across multiple projects.

When a user sends you a message, analyze it and respond with ONLY a JSON object.
No extra text, no markdown fences — just the raw JSON.

JSON structure:
{
  "action": "chat" | "save_note" | "create_task" | "list_tasks" | "list_notes" | "complete_task" | "set_reminder" | "create_project" | "switch_project",
  "message": "your friendly, concise response to show the user",
  "data": {
    // Fields depend on action:
    // save_note    → { "content": "the note text" }
    // create_task  → { "title": "short title ≤60 chars", "description": "detail or null", "reminder_at": "YYYY-MM-DD HH:MM:SS or null" }
    // complete_task→ { "task_id": number or null }
    // set_reminder → { "task_id": number or null, "reminder_at": "YYYY-MM-DD HH:MM:SS" }
    // create_project → { "name": "project name" }
    // switch_project → { "name": "project name" }
    // chat / list_tasks / list_notes → {}
  }
}

Rules:
- Respond ONLY with valid JSON. Never add text outside the JSON.
- Be warm and concise in the "message" field.
- For datetimes use: YYYY-MM-DD HH:MM:SS
- If you're unsure of the action, use "chat".
- Never invent task IDs.
- If the user is saving a reminder or mentioning a time, use "create_task" with reminder_at set."""


def process_message(user_message: str, context: str = "") -> dict:
    """Send a user message to Claude and get a structured action back."""
    prompt = f"{context}\n\nUser: {user_message}" if context else f"User: {user_message}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if model wraps in them
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])

    return json.loads(raw)


def convert_notes_to_tasks(notes: list, project_name: str) -> list:
    """Ask Claude to extract actionable tasks from a list of raw notes."""
    notes_text = "\n".join([f"- {n['content']}" for n in notes])

    prompt = f"""You are a productivity assistant. Extract clear, actionable tasks from these notes taken in the '{project_name}' project.

Notes:
{notes_text}

Return ONLY a JSON array. Each item must have:
- "title": short task title (max 60 chars)
- "description": optional extra detail (null if not needed)

Example output:
[
  {{"title": "Follow up with John re: partnership", "description": null}},
  {{"title": "Review Q2 budget proposal", "description": "Deadline: end of week"}}
]

Return ONLY the JSON array. No other text."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])

    return json.loads(raw)
