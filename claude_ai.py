import anthropic
import os
import json
import re

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-5"


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
    Returns list of {title, description, tags, source_note_id}.
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
  "source_note_id": <integer note_id or null>
}}

Return ONLY the JSON array. No other text."""}]
    )

    return _parse_json(response.content[0].text)


def raw_input_to_task(raw_text: str, project_name: str) -> dict:
    """Convert free-form task input into a structured task dict.

    Returns {title, description, tags}.
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": f"""You are a productivity assistant.
Convert this rough input into a structured task for the '{project_name}' project.

Input: {raw_text}

Return ONLY a JSON object:
{{
  "title": "short task title (max 60 chars)",
  "description": "extra detail or null",
  "tags": "comma-separated tags extracted from #hashtags or empty string"
}}

Return ONLY the JSON. No other text."""}]
    )

    return _parse_json(response.content[0].text)
