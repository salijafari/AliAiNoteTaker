import os
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv

import database as db
import claude_ai as ai

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id)
    projects = db.get_projects(user_id)

    if not projects:
        text = (
            "👋 *Welcome to your AI Productivity Bot!*\n\n"
            "I'm powered by Claude and I'll keep you organised across all your projects — "
            "YVR, your startup, personal, whatever you need.\n\n"
            "Let's start! *What's your first project called?*\n"
            "_(Just type the name, e.g. YVR, Evalyze, Personal)_"
        )
    else:
        active = db.get_active_project(user_id)
        active_name = active["name"] if active else "None selected"
        text = (
            f"👋 *Welcome back!*\n\n"
            f"📂 Active project: *{active_name}*\n\n"
            "Here's what you can do:\n"
            "📝 `/note your thought` — save a note\n"
            "📋 `/notes` — view recent notes\n"
            "✅ `/tasks` — view + manage tasks\n"
            "📂 `/projects` — switch projects\n"
            "🔄 `/convert` — turn notes into tasks\n"
            "➕ `/newproject Name` — create a project\n\n"
            "_Or just talk to me naturally — I understand plain English!_"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
# /note
# ─────────────────────────────────────────────
async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id)
    active = db.get_active_project(user_id)

    if not active:
        await update.message.reply_text(
            "⚠️ No active project yet.\nUse `/newproject YourProjectName` to create one.",
            parse_mode="Markdown",
        )
        return

    content = " ".join(context.args)
    if not content:
        await update.message.reply_text(
            "📝 Usage: `/note your thought here`",
            parse_mode="Markdown",
        )
        return

    db.add_note(user_id, active["id"], content)
    await update.message.reply_text(
        f"✅ Note saved to *{active['name']}*\n\n_{content}_",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# /notes
# ─────────────────────────────────────────────
async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    active = db.get_active_project(user_id)

    if not active:
        await update.message.reply_text("⚠️ No active project. Use `/newproject` to create one.")
        return

    notes = db.get_notes(user_id, active["id"])

    if not notes:
        await update.message.reply_text(
            f"📭 No notes in *{active['name']}* yet.\nTry: `/note meeting with John tomorrow`",
            parse_mode="Markdown",
        )
        return

    text = f"📋 *Recent notes — {active['name']}:*\n\n"
    for i, note in enumerate(notes, 1):
        created = note["created_at"][:10]
        text += f"{i}. {note['content']} _(_{created}_)_\n"
    text += "\n_Use /convert to turn these into tasks!_"

    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
# /tasks
# ─────────────────────────────────────────────
async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    active = db.get_active_project(user_id)

    if not active:
        await update.message.reply_text("⚠️ No active project. Use `/newproject` first.")
        return

    tasks = db.get_tasks(user_id, active["id"])

    if not tasks:
        await update.message.reply_text(
            f"🎉 No pending tasks in *{active['name']}*!\n"
            "Add notes with `/note` then run `/convert` to generate tasks.",
            parse_mode="Markdown",
        )
        return

    text = f"✅ *Tasks — {active['name']}:*\n\n"
    keyboard = []

    for task in tasks:
        reminder_str = ""
        if task["reminder_at"]:
            reminder_str = f" ⏰ {task['reminder_at'][:16]}"
        text += f"*#{task['id']}* {task['title']}{reminder_str}\n"
        if task["description"]:
            text += f"   _{task['description']}_\n"
        text += "\n"

        keyboard.append([
            InlineKeyboardButton(f"✅ Done #{task['id']}", callback_data=f"done_{task['id']}"),
            InlineKeyboardButton(f"⏰ Remind #{task['id']}", callback_data=f"remind_{task['id']}"),
        ])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ─────────────────────────────────────────────
# /projects
# ─────────────────────────────────────────────
async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    projects = db.get_projects(user_id)
    active = db.get_active_project(user_id)

    if not projects:
        await update.message.reply_text(
            "📂 No projects yet.\nUse `/newproject YourProjectName` to create one!",
            parse_mode="Markdown",
        )
        return

    text = "📂 *Your Projects:*\n\n"
    keyboard = []

    for p in projects:
        is_active = active and active["id"] == p["id"]
        marker = "▶️ " if is_active else ""
        text += f"{marker}*{p['name']}*\n"
        if not is_active:
            keyboard.append([
                InlineKeyboardButton(f"Switch to {p['name']}", callback_data=f"switch_{p['id']}")
            ])

    if keyboard:
        text += "\n_Tap a button to switch projects_"

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


# ─────────────────────────────────────────────
# /newproject
# ─────────────────────────────────────────────
async def new_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id)
    name = " ".join(context.args).strip()

    if not name:
        await update.message.reply_text(
            "Usage: `/newproject YourProjectName`", parse_mode="Markdown"
        )
        return

    db.create_project(user_id, name)
    await update.message.reply_text(
        f"🎉 Project *{name}* created and set as active!\n\n"
        "📝 `/note` — add your first note\n"
        "✅ `/tasks` — view tasks",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# /convert  — AI turns notes → tasks
# ─────────────────────────────────────────────
async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    active = db.get_active_project(user_id)

    if not active:
        await update.message.reply_text("⚠️ No active project. Use `/newproject` first.")
        return

    notes = db.get_notes(user_id, active["id"])

    if not notes:
        await update.message.reply_text(
            f"📭 No notes in *{active['name']}* to convert yet.\n"
            "Add some with `/note your idea`!",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("🤔 Claude is analysing your notes… give me a second!")

    try:
        tasks = ai.convert_notes_to_tasks(notes, active["name"])

        if not tasks:
            await update.message.reply_text(
                "😕 Couldn't extract clear tasks from your notes. "
                "Try adding more specific notes!"
            )
            return

        text = f"🎯 *Claude found {len(tasks)} tasks in {active['name']}:*\n\n"
        keyboard = []

        for i, task in enumerate(tasks):
            text += f"*{i + 1}.* {task['title']}\n"
            if task.get("description"):
                text += f"   _{task['description']}_\n"
            text += "\n"
            keyboard.append([
                InlineKeyboardButton(f"💾 Save task {i + 1}", callback_data=f"savetask_{i}")
            ])

        context.user_data["pending_tasks"] = tasks
        context.user_data["pending_project_id"] = active["id"]

        keyboard.append([
            InlineKeyboardButton("💾 Save ALL tasks", callback_data="savetask_all")
        ])

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Convert error: {e}")
        await update.message.reply_text("❌ Something went wrong. Try again in a moment!")


# ─────────────────────────────────────────────
# Inline button callbacks
# ─────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # ── Mark task done ──
    if data.startswith("done_"):
        task_id = int(data.split("_")[1])
        db.complete_task(task_id, user_id)
        await query.edit_message_text(f"✅ Task #{task_id} marked as done — great work! 🎉")

    # ── Choose reminder time ──
    elif data.startswith("remind_"):
        task_id = int(data.split("_")[1])
        keyboard = [
            [
                InlineKeyboardButton("In 1 hour", callback_data=f"setremind_{task_id}_1h"),
                InlineKeyboardButton("In 3 hours", callback_data=f"setremind_{task_id}_3h"),
            ],
            [
                InlineKeyboardButton("Tomorrow 9 AM", callback_data=f"setremind_{task_id}_tomorrow"),
                InlineKeyboardButton("In 1 week", callback_data=f"setremind_{task_id}_1w"),
            ],
        ]
        await query.edit_message_text(
            f"⏰ When should I remind you about task #{task_id}?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ── Set the actual reminder ──
    elif data.startswith("setremind_"):
        parts = data.split("_")
        task_id = int(parts[1])
        when = parts[2]
        now = datetime.now()

        if when == "1h":
            remind_at = now + timedelta(hours=1)
        elif when == "3h":
            remind_at = now + timedelta(hours=3)
        elif when == "tomorrow":
            remind_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        elif when == "1w":
            remind_at = now + timedelta(weeks=1)
        else:
            remind_at = now + timedelta(hours=1)

        db.set_task_reminder(task_id, user_id, remind_at.strftime("%Y-%m-%d %H:%M:%S"))
        await query.edit_message_text(
            f"⏰ Set! I'll remind you about task #{task_id} on "
            f"*{remind_at.strftime('%b %d at %H:%M')}* 👍",
            parse_mode="Markdown",
        )

    # ── Switch project ──
    elif data.startswith("switch_"):
        project_id = int(data.split("_")[1])
        db.set_active_project(user_id, project_id)
        projects = db.get_projects(user_id)
        proj_name = next((p["name"] for p in projects if p["id"] == project_id), "Unknown")
        await query.edit_message_text(
            f"✅ Switched to project *{proj_name}*!", parse_mode="Markdown"
        )

    # ── Save converted task(s) ──
    elif data.startswith("savetask_"):
        pending = context.user_data.get("pending_tasks", [])
        project_id = context.user_data.get("pending_project_id")
        which = data.split("_")[1]

        if which == "all":
            for task in pending:
                db.add_task(user_id, project_id, task["title"], task.get("description"))
            await query.edit_message_text(
                f"💾 All {len(pending)} tasks saved! Use /tasks to view them."
            )
        else:
            idx = int(which)
            if idx < len(pending):
                task = pending[idx]
                db.add_task(user_id, project_id, task["title"], task.get("description"))
                await query.edit_message_text(
                    f"💾 Saved: *{task['title']}*\n\nUse /tasks to manage it.",
                    parse_mode="Markdown",
                )


# ─────────────────────────────────────────────
# Natural language handler
# ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id)
    active = db.get_active_project(user_id)
    text = update.message.text

    # First-time setup: no projects → treat message as project name
    if not active and not db.get_projects(user_id):
        name = text.strip()
        db.create_project(user_id, name)
        await update.message.reply_text(
            f"🎉 Project *{name}* created!\n\n"
            "Now try:\n"
            "📝 `/note` — save a quick thought\n"
            "✅ `/tasks` — view tasks\n"
            "📂 `/projects` — manage projects\n\n"
            "_Or just talk to me naturally!_",
            parse_mode="Markdown",
        )
        return

    # Build context for Claude
    ctx_parts = []
    if active:
        ctx_parts.append(f"Active project: {active['name']}")
        tasks = db.get_tasks(user_id, active["id"])
        if tasks:
            task_list = ", ".join([f"#{t['id']} {t['title']}" for t in tasks[:5]])
            ctx_parts.append(f"Pending tasks: {task_list}")
    ctx = "\n".join(ctx_parts)

    try:
        result = ai.process_message(text, ctx)
        action = result.get("action", "chat")
        message = result.get("message", "I'm here to help!")
        data = result.get("data", {})

        if action == "save_note" and active:
            content = data.get("content", text)
            db.add_note(user_id, active["id"], content)
            await update.message.reply_text(f"📝 {message}")

        elif action == "create_task" and active:
            title = data.get("title", text[:60])
            description = data.get("description")
            reminder_at = data.get("reminder_at")
            db.add_task(user_id, active["id"], title, description, reminder_at)
            await update.message.reply_text(f"✅ {message}")

        elif action == "list_tasks":
            await tasks_command(update, context)

        elif action == "list_notes":
            await notes_command(update, context)

        elif action == "create_project":
            name = data.get("name", text).strip()
            db.create_project(user_id, name)
            await update.message.reply_text(f"📂 {message}")

        elif action == "switch_project":
            name = data.get("name", "").strip().lower()
            projects = db.get_projects(user_id)
            match = next((p for p in projects if p["name"].lower() == name), None)
            if match:
                db.set_active_project(user_id, match["id"])
                await update.message.reply_text(f"📂 {message}")
            else:
                await update.message.reply_text(
                    f"❓ Couldn't find project '{name}'. Use /projects to see your list."
                )

        else:
            await update.message.reply_text(message)

    except Exception as e:
        logger.error(f"Message handler error: {e}")
        await update.message.reply_text(
            "🤔 Sorry, I had a hiccup. Try a command like `/note`, `/tasks`, or `/projects`!",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────
# Reminder job (runs every 60 seconds)
# ─────────────────────────────────────────────
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    due = db.get_due_reminders()
    for task in due:
        try:
            desc_line = f"_{task['description']}_\n" if task.get("description") else ""
            await context.bot.send_message(
                chat_id=task["user_id"],
                text=(
                    f"⏰ *Reminder!*\n\n"
                    f"📂 Project: *{task['project_name']}*\n"
                    f"📌 *{task['title']}*\n"
                    f"{desc_line}"
                    f"\nStill pending — use /tasks to manage it."
                ),
                parse_mode="Markdown",
            )
            db.mark_reminded(task["id"])
        except Exception as e:
            logger.error(f"Reminder send error for task {task['id']}: {e}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("projects", projects_command))
    app.add_handler(CommandHandler("newproject", new_project_command))
    app.add_handler(CommandHandler("convert", convert_command))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Natural language (any non-command text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Reminder check every 60 seconds
    app.job_queue.run_repeating(check_reminders, interval=60, first=15)

    logger.info("🤖 Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
