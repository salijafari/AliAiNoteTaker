import os
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ConversationHandler,
    TypeHandler,
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

BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
_admin_raw   = os.getenv("ADMIN_USER_ID", "")
ADMIN_USER_ID = int(_admin_raw) if _admin_raw.strip().isdigit() else None

# ── Conversation states ────────────────────────────────────────────────────────
(
    NOTE_PICK_PROJECT,
    NOTE_AWAIT_TEXT,
    TASK_PICK_PROJECT,
    TASK_PICK_MODE,
    TASK_AWAIT_TEXT,
    TASK_PICK_NOTES,
    PROJECT_PICK,
    PROJECT_ACTION,
    NEWPROJECT_AWAIT_NAME,
    TASK_REVIEW_SUGGESTED,
    TASK_VIEW_LIST,
    TASK_EDIT_CONTENT,
    TASK_EDIT_DEADLINE,
    CHATPROJECTS_PICK_ACTION,
) = range(14)

# Keys stored in context.user_data during flows
_FLOW           = "flow"
_PROJECT_ID     = "flow_project_id"
_PENDING        = "pending_tasks"
_NOTE_IDS       = "selected_note_ids"
_SUGGESTED_IDS  = "selected_suggested_ids"
_EDIT_TASK_ID   = "edit_task_id"
_CHAT_OWNER_ID  = "chat_owner_id"
_CHAT_ID_KEY    = "chat_id_flow"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _project_keyboard(projects: list, show_create: bool = True):
    """Build an inline keyboard of project buttons + optional '+ Create Project'."""
    kb = [[InlineKeyboardButton(p["name"], callback_data=f"proj_{p['id']}")] for p in projects]
    if show_create:
        kb.append([InlineKeyboardButton("➕ Create Project", callback_data="proj_new")])
    return InlineKeyboardMarkup(kb)


def _fmt_note(note: dict, show_raw: bool = False) -> str:
    tags = f"  🏷 {note['tags']}" if note.get("tags") else ""
    ts   = note["created_at"][:16]
    text = f"📌 *{note['refined_text']}*\n_{ts}{tags}_"
    if show_raw and note["raw_text"] != note["refined_text"]:
        text += f"\n\n_Raw: {note['raw_text']}_"
    return text


def _fmt_task(task: dict) -> str:
    tags     = f"  🏷 {task['tags']}" if task.get("tags") else ""
    ts       = task["created_at"][:10]
    desc     = f"\n   _{task['description']}_" if task.get("description") else ""
    deadline = f"\n   📅 Due: {task['deadline']}" if task.get("deadline") else ""
    return f"*#{task['id']}* {task['title']}{desc}{deadline}\n_{ts}{tags}_"


async def _resolve_chat_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Determine the owner user_id and chat_id for the current interaction.

    - Private chat: registers the chat, returns (user_id, chat_id).
    - Group chat not set up: sends a setup message and returns None.
    - Group chat set up: returns (owner_user_id, chat_id).
    Stores _CHAT_OWNER_ID and _CHAT_ID_KEY in context.user_data.
    """
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    user_id = user.id

    if chat.type == "private":
        db.register_chat(chat_id, "private", user.full_name, user_id)
        context.user_data[_CHAT_OWNER_ID] = user_id
        context.user_data[_CHAT_ID_KEY]   = chat_id
        return (user_id, chat_id)

    # Group / supergroup
    chat_record = db.get_chat(chat_id)
    if not chat_record or not chat_record["setup_complete"]:
        msg = (
            "⚠️ This chat hasn't been set up yet.\n"
            "An admin must run /chatprojects to configure which projects are accessible here."
        )
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return None

    owner_id = chat_record["created_by_user_id"]
    context.user_data[_CHAT_OWNER_ID] = owner_id
    context.user_data[_CHAT_ID_KEY]   = chat_id
    return (owner_id, chat_id)


async def _is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def _send_project_picker(update_or_query, user_id: int, prompt: str, chat_id: int = None):
    projects  = db.get_projects(user_id)
    in_group  = False

    if chat_id:
        chat_record = db.get_chat(chat_id)
        if chat_record and chat_record["chat_type"] != "private":
            in_group = True
            allowed  = {p["id"] for p in db.get_chat_projects(chat_id)}
            projects = [p for p in projects if p["id"] in allowed]

    kb = _project_keyboard(projects, show_create=not in_group)
    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(prompt, reply_markup=kb, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(prompt, reply_markup=kb, parse_mode="Markdown")


# ── Access guard (runs before every handler) ─────────────────────────────────

async def access_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block any user/chat not on the whitelist. Admin always passes."""
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None

    # Master admin always allowed
    if ADMIN_USER_ID and user_id == ADMIN_USER_ID:
        return

    # Whitelisted user or whitelisted chat
    if user_id and db.is_whitelisted("user", user_id):
        return
    if chat_id and chat_id != user_id and db.is_whitelisted("chat", chat_id):
        return

    # Denied — respond once and stop all further processing
    if update.message:
        await update.message.reply_text("⛔ Access denied.")
    elif update.callback_query:
        await update.callback_query.answer("⛔ Access denied.", show_alert=True)
    raise ApplicationHandlerStop


# ── Admin helpers ─────────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID


# ── Admin commands ────────────────────────────────────────────────────────────

async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /adduser <user_id>")
        return
    uid = int(context.args[0])
    added = db.add_to_whitelist("user", uid, update.effective_user.id)
    if added:
        await update.message.reply_text(f"✅ User `{uid}` added to whitelist.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ User `{uid}` is already whitelisted.", parse_mode="Markdown")


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /removeuser <user_id>")
        return
    uid = int(context.args[0])
    if uid == ADMIN_USER_ID:
        await update.message.reply_text("⛔ Cannot remove the master admin.")
        return
    removed = db.remove_from_whitelist("user", uid)
    if removed:
        await update.message.reply_text(f"🗑 User `{uid}` removed from whitelist.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ User `{uid}` was not on the whitelist.", parse_mode="Markdown")


async def cmd_addchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    chat_id = update.effective_chat.id
    title   = update.effective_chat.title or str(chat_id)
    added   = db.add_to_whitelist("chat", chat_id, update.effective_user.id)
    if added:
        await update.message.reply_text(f"✅ Chat *{title}* (`{chat_id}`) added to whitelist.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ This chat is already whitelisted.", parse_mode="Markdown")


async def cmd_removechat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    chat_id = update.effective_chat.id
    title   = update.effective_chat.title or str(chat_id)
    removed = db.remove_from_whitelist("chat", chat_id)
    if removed:
        await update.message.reply_text(f"🗑 Chat *{title}* removed from whitelist.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ This chat was not on the whitelist.", parse_mode="Markdown")


async def cmd_listaccess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    entries = db.get_whitelist()
    if not entries:
        await update.message.reply_text("📋 Whitelist is empty.")
        return
    users = [e for e in entries if e["type"] == "user"]
    chats = [e for e in entries if e["type"] == "chat"]
    lines = [f"📋 *Whitelist* ({len(entries)} entries)\n"]
    if users:
        lines.append("*Users:*")
        for e in users:
            admin_tag = " _(master admin)_" if e["telegram_id"] == ADMIN_USER_ID else ""
            lines.append(f"  • `{e['telegram_id']}`{admin_tag} — added {e['added_at'][:10]}")
    if chats:
        lines.append("\n*Chats:*")
        for e in chats:
            lines.append(f"  • `{e['telegram_id']}` — added {e['added_at'][:10]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome!*\n\n"
        "Here's what you can do:\n"
        "📝 /note — capture a note\n"
        "✅ /task — create or convert tasks\n"
        "📂 /project — browse your projects\n\n"
        "_Everything is organised by project. Let's go!_",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# /note  flow
# ══════════════════════════════════════════════════════════════════════════════

async def note_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[_FLOW] = "note"
    result = await _resolve_chat_context(update, context)
    if result is None:
        return ConversationHandler.END
    user_id, chat_id = result
    await _send_project_picker(update, user_id, "📝 *New note — pick a project:*", chat_id=chat_id)
    return NOTE_PICK_PROJECT


async def note_project_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)

    if query.data == "proj_new":
        context.user_data["after_newproject"] = "note"
        await query.edit_message_text("🗂 *New project name:*", parse_mode="Markdown")
        return NEWPROJECT_AWAIT_NAME

    project_id = int(query.data.split("_")[1])
    project    = db.get_project(project_id, user_id)
    context.user_data[_PROJECT_ID] = project_id

    notes = db.get_notes(user_id, project_id, limit=5)
    text  = f"📂 *{project['name']}*\n\n"
    if notes:
        text += "_Recent notes:_\n"
        for n in notes:
            ts = n["created_at"][:10]
            text += f"• {n['refined_text']} _{ts}_\n"
        text += "\n"
    text += "✏️ *Type your note now:*\n_Hashtags like #vendor will be saved as tags._"

    await query.edit_message_text(text, parse_mode="Markdown")
    return NOTE_AWAIT_TEXT


async def note_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = context.user_data.get(_CHAT_OWNER_ID, update.effective_user.id)
    raw_text   = update.message.text.strip()
    project_id = context.user_data.get(_PROJECT_ID)

    if not project_id:
        await update.message.reply_text("Something went wrong. Please start again with /note.")
        return ConversationHandler.END

    project = db.get_project(project_id, user_id)
    await update.message.reply_text("✨ Refining your note…")

    try:
        refined = ai.refine_note(raw_text, project["name"])
    except Exception as e:
        logger.error(f"refine_note error: {e}")
        refined = raw_text

    tags = ai.extract_hashtags(raw_text)
    db.add_note(user_id, project_id, raw_text, refined, tags)

    tag_line = f"\n🏷 Tags: {tags}" if tags else ""
    await update.message.reply_text(
        f"✅ *Note saved to {project['name']}*\n\n"
        f"{refined}{tag_line}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# /task  flow
# ══════════════════════════════════════════════════════════════════════════════

async def task_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[_FLOW] = "task"
    result = await _resolve_chat_context(update, context)
    if result is None:
        return ConversationHandler.END
    user_id, chat_id = result
    await _send_project_picker(update, user_id, "✅ *Tasks — pick a project:*", chat_id=chat_id)
    return TASK_PICK_PROJECT


async def task_project_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)

    if query.data == "proj_new":
        context.user_data["after_newproject"] = "task"
        await query.edit_message_text("🗂 *New project name:*", parse_mode="Markdown")
        return NEWPROJECT_AWAIT_NAME

    project_id = int(query.data.split("_")[1])
    project    = db.get_project(project_id, user_id)
    context.user_data[_PROJECT_ID] = project_id

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Write a new task",        callback_data="taskmode_new")],
        [InlineKeyboardButton("🔄 Generate from notes",     callback_data="taskmode_convert")],
        [InlineKeyboardButton("📋 View existing tasks",     callback_data="taskmode_view")],
    ])
    await query.edit_message_text(
        f"📂 *{project['name']}* — what would you like to do?",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return TASK_PICK_MODE


async def task_mode_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    await query.answer()
    user_id    = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)
    project_id = context.user_data[_PROJECT_ID]
    project    = db.get_project(project_id, user_id)

    if query.data == "taskmode_new":
        await query.edit_message_text(
            f"✏️ *New task for {project['name']}:*\n"
            "_Hashtags like #design will be saved as tags._\n"
            "_Mention a date (e.g. 'by Friday') to set a deadline._",
            parse_mode="Markdown",
        )
        return TASK_AWAIT_TEXT

    if query.data == "taskmode_view":
        async def _edit(text, reply_markup=None):
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        return await _show_tasks(_edit, user_id, project_id)

    # Generate from notes — show note picker
    notes = db.get_notes(user_id, project_id, limit=10)
    if not notes:
        await query.edit_message_text(
            "📭 No notes in this project yet. Add some with /note first!"
        )
        return ConversationHandler.END

    context.user_data[_NOTE_IDS]     = []
    context.user_data["notes_cache"] = {n["id"]: n for n in notes}
    kb = [
        [InlineKeyboardButton(f"⬜ {n['refined_text'][:50]}", callback_data=f"picknote_{n['id']}")]
        for n in notes
    ]
    kb.append([InlineKeyboardButton("✅ Convert selected notes", callback_data="picknote_done")])
    await query.edit_message_text(
        "📋 *Select notes to convert into tasks:*\n_Tap to toggle, then press Convert._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return TASK_PICK_NOTES


async def task_note_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "picknote_done":
        selected_ids = context.user_data.get(_NOTE_IDS, [])
        if not selected_ids:
            await query.answer("Select at least one note first.", show_alert=True)
            return TASK_PICK_NOTES
        await query.answer()

        user_id     = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)
        project_id  = context.user_data[_PROJECT_ID]
        project     = db.get_project(project_id, user_id)
        notes_cache = context.user_data.get("notes_cache", {})
        selected_notes = [notes_cache[nid] for nid in selected_ids if nid in notes_cache]

        await query.edit_message_text("🤔 Claude is building tasks from your notes…")

        try:
            tasks = ai.notes_to_tasks(selected_notes, project["name"])
        except Exception as e:
            logger.error(f"notes_to_tasks error: {e}")
            await query.edit_message_text("❌ Something went wrong. Please try again.")
            return ConversationHandler.END

        context.user_data[_PENDING]       = tasks
        context.user_data[_SUGGESTED_IDS] = list(range(len(tasks)))

        text = f"🎯 *Claude found {len(tasks)} tasks for {project['name']}:*\n_Tap to deselect, then save._\n\n"
        kb   = []
        for i, t in enumerate(tasks):
            deadline_str = f"  📅 {t['deadline']}" if t.get("deadline") else ""
            desc = f"\n   _{t['description']}_" if t.get("description") else ""
            text += f"*{i+1}.* {t['title']}{deadline_str}{desc}\n\n"
            kb.append([InlineKeyboardButton(f"☑️ {t['title'][:45]}{deadline_str}", callback_data=f"stask_{i}")])
        kb.append([InlineKeyboardButton("💾 Save selected tasks", callback_data="stask_done")])

        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return TASK_REVIEW_SUGGESTED

    # Toggle a note
    await query.answer()
    note_id  = int(query.data.split("_")[1])
    selected = context.user_data.setdefault(_NOTE_IDS, [])
    if note_id in selected:
        selected.remove(note_id)
    else:
        selected.append(note_id)

    notes_cache = context.user_data.get("notes_cache", {})
    notes = list(notes_cache.values())
    kb = [
        [InlineKeyboardButton(
            f"{'☑️' if n['id'] in selected else '⬜'} {n['refined_text'][:50]}",
            callback_data=f"picknote_{n['id']}"
        )]
        for n in notes
    ]
    kb.append([InlineKeyboardButton("✅ Convert selected notes", callback_data="picknote_done")])
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
    return TASK_PICK_NOTES


async def task_suggested_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "stask_done":
        selected_idxs = context.user_data.get(_SUGGESTED_IDS, [])
        if not selected_idxs:
            await query.answer("Select at least one task first.", show_alert=True)
            return TASK_REVIEW_SUGGESTED
        await query.answer()

        user_id    = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)
        project_id = context.user_data[_PROJECT_ID]
        pending    = context.user_data.get(_PENDING, [])

        for idx in sorted(selected_idxs):
            if idx < len(pending):
                t = pending[idx]
                db.add_task(
                    user_id, project_id, t["title"],
                    t.get("description"), t.get("tags", ""),
                    t.get("source_note_id"), t.get("deadline")
                )
        await query.edit_message_text(f"💾 *{len(selected_idxs)} task(s) saved!*", parse_mode="Markdown")
        return ConversationHandler.END

    # Toggle a suggested task
    await query.answer()
    idx      = int(query.data.split("_")[1])
    selected = context.user_data.setdefault(_SUGGESTED_IDS, [])
    if idx in selected:
        selected.remove(idx)
    else:
        selected.append(idx)

    pending = context.user_data.get(_PENDING, [])
    kb = []
    for i, t in enumerate(pending):
        deadline_str = f"  📅 {t['deadline']}" if t.get("deadline") else ""
        checked = i in selected
        kb.append([InlineKeyboardButton(
            f"{'☑️' if checked else '⬜'} {t['title'][:45]}{deadline_str}",
            callback_data=f"stask_{i}"
        )])
    kb.append([InlineKeyboardButton("💾 Save selected tasks", callback_data="stask_done")])
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
    return TASK_REVIEW_SUGGESTED


async def task_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = context.user_data.get(_CHAT_OWNER_ID, update.effective_user.id)
    raw_text   = update.message.text.strip()
    project_id = context.user_data.get(_PROJECT_ID)

    if not project_id:
        await update.message.reply_text("Something went wrong. Please start again with /task.")
        return ConversationHandler.END

    project = db.get_project(project_id, user_id)
    await update.message.reply_text("✨ Building your task…")

    try:
        results = ai.raw_input_to_tasks(raw_text, project["name"])
        if not isinstance(results, list):
            results = [results]
    except Exception as e:
        logger.error(f"raw_input_to_tasks error: {e}")
        results = [{"title": raw_text[:60], "description": None, "tags": ""}]

    saved_lines = []
    for result in results:
        tags     = result.get("tags") or ai.extract_hashtags(raw_text)
        deadline = result.get("deadline")
        db.add_task(user_id, project_id, result["title"], result.get("description"), tags,
                    deadline=deadline)
        tag_line      = f" 🏷 {tags}" if tags else ""
        deadline_line = f" 📅 {deadline}" if deadline else ""
        saved_lines.append(f"• *{result['title']}*{deadline_line}{tag_line}")

    reply = f"✅ *{len(results)} task(s) saved to {project['name']}*\n\n" + "\n".join(saved_lines)
    await update.message.reply_text(reply, parse_mode="Markdown")
    return ConversationHandler.END


# ── Task view / edit / deadline ───────────────────────────────────────────────

def _build_task_list_kb(tasks):
    """Build inline keyboard with Done + Edit buttons for each task."""
    kb = []
    for t in tasks:
        kb.append([
            InlineKeyboardButton("✅ Done", callback_data=f"tv_done_{t['id']}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"tv_edit_{t['id']}"),
        ])
    return kb


async def _show_tasks(send_fn, user_id, project_id, header=""):
    """Fetch pending tasks and call send_fn(text, reply_markup). Returns the state."""
    project = db.get_project(project_id, user_id)
    tasks   = db.get_tasks(user_id, project_id)
    if not tasks:
        await send_fn(
            f"{header}🎉 All tasks in *{project['name']}* are done!",
            reply_markup=None,
        )
        return ConversationHandler.END
    text = f"{header}✅ *Pending tasks — {project['name']}:*\n\n"
    for t in tasks:
        text += _fmt_task(t) + "\n\n"
    kb = _build_task_list_kb(tasks)
    await send_fn(text, reply_markup=InlineKeyboardMarkup(kb))
    return TASK_VIEW_LIST


async def task_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    user_id    = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)
    project_id = context.user_data.get(_PROJECT_ID)
    data       = query.data

    async def _edit(text, reply_markup=None):
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    # ── Mark done ──
    if data.startswith("tv_done_"):
        await query.answer("Marked as done!")
        task_id = int(data.split("_")[2])
        db.complete_task(task_id, user_id)
        return await _show_tasks(_edit, user_id, project_id)

    # ── Edit sub-menu ──
    if data.startswith("tv_edit_"):
        await query.answer()
        task_id = int(data.split("_")[2])
        context.user_data[_EDIT_TASK_ID] = task_id
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Edit content",    callback_data=f"tv_ec_{task_id}")],
            [InlineKeyboardButton("📅 Change deadline",  callback_data=f"tv_dl_{task_id}")],
            [InlineKeyboardButton("◀️ Back to tasks",    callback_data="tv_back")],
        ])
        await _edit(f"✏️ *Editing task #{task_id}* — what do you want to change?", reply_markup=kb)
        return TASK_VIEW_LIST

    # ── Prompt for new content ──
    if data.startswith("tv_ec_"):
        await query.answer()
        task_id = int(data.split("_")[2])
        context.user_data[_EDIT_TASK_ID] = task_id
        await _edit(
            f"📝 *Type the updated content for task #{task_id}:*\n"
            "_Hashtags and deadlines will be extracted automatically._"
        )
        return TASK_EDIT_CONTENT

    # ── Deadline sub-menu ──
    if data.startswith("tv_dl_"):
        await query.answer()
        task_id   = int(data.split("_")[2])
        context.user_data[_EDIT_TASK_ID] = task_id
        tomorrow  = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        next_week = (datetime.now() + timedelta(weeks=1)).strftime("%Y-%m-%d")
        two_weeks = (datetime.now() + timedelta(weeks=2)).strftime("%Y-%m-%d")
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"Tomorrow ({tomorrow})",   callback_data=f"tv_sd_{task_id}_{tomorrow}"),
                InlineKeyboardButton(f"Next week ({next_week})", callback_data=f"tv_sd_{task_id}_{next_week}"),
            ],
            [
                InlineKeyboardButton(f"2 weeks ({two_weeks})",   callback_data=f"tv_sd_{task_id}_{two_weeks}"),
                InlineKeyboardButton("✏️ Custom date",           callback_data=f"tv_cd_{task_id}"),
            ],
            [
                InlineKeyboardButton("🗑 Remove deadline", callback_data=f"tv_rd_{task_id}"),
                InlineKeyboardButton("◀️ Back",            callback_data="tv_back"),
            ],
        ])
        await _edit(f"📅 *Set deadline for task #{task_id}:*", reply_markup=kb)
        return TASK_VIEW_LIST

    # ── Set deadline from button ──
    if data.startswith("tv_sd_"):
        await query.answer()
        parts    = data.split("_")
        task_id  = int(parts[2])
        deadline = parts[3]
        db.update_task_deadline(task_id, user_id, deadline)
        return await _show_tasks(_edit, user_id, project_id, header=f"📅 Deadline set to {deadline}.\n\n")

    # ── Custom deadline (text input) ──
    if data.startswith("tv_cd_"):
        await query.answer()
        task_id = int(data.split("_")[2])
        context.user_data[_EDIT_TASK_ID] = task_id
        await _edit(f"📅 *Type the deadline for task #{task_id}:*\n_(Format: YYYY-MM-DD)_")
        return TASK_EDIT_DEADLINE

    # ── Remove deadline ──
    if data.startswith("tv_rd_"):
        await query.answer()
        task_id = int(data.split("_")[2])
        db.update_task_deadline(task_id, user_id, None)
        return await _show_tasks(_edit, user_id, project_id, header="🗑 Deadline removed.\n\n")

    # ── Back to list ──
    if data == "tv_back":
        await query.answer()
        return await _show_tasks(_edit, user_id, project_id)

    await query.answer()
    return TASK_VIEW_LIST


async def task_edit_content_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = context.user_data.get(_CHAT_OWNER_ID, update.effective_user.id)
    raw_text   = update.message.text.strip()
    task_id    = context.user_data.get(_EDIT_TASK_ID)
    project_id = context.user_data.get(_PROJECT_ID)

    if not task_id or not project_id:
        await update.message.reply_text("Something went wrong. Use /task to start again.")
        return ConversationHandler.END

    project = db.get_project(project_id, user_id)
    await update.message.reply_text("✨ Updating task…")

    try:
        results = ai.raw_input_to_tasks(raw_text, project["name"])
        if not isinstance(results, list):
            results = [results]
        result = results[0]
    except Exception as e:
        logger.error(f"edit task error: {e}")
        result = {"title": raw_text[:60], "description": None, "tags": ""}

    tags = result.get("tags") or ai.extract_hashtags(raw_text)
    db.update_task_content(task_id, user_id, result["title"], result.get("description"), tags)
    if result.get("deadline"):
        db.update_task_deadline(task_id, user_id, result["deadline"])

    async def _reply(text, reply_markup=None):
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    return await _show_tasks(_reply, user_id, project_id, header=f"✅ Task #{task_id} updated.\n\n")


async def task_edit_deadline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id    = context.user_data.get(_CHAT_OWNER_ID, update.effective_user.id)
    raw_text   = update.message.text.strip()
    task_id    = context.user_data.get(_EDIT_TASK_ID)
    project_id = context.user_data.get(_PROJECT_ID)

    if not task_id or not project_id:
        await update.message.reply_text("Something went wrong. Use /task to start again.")
        return ConversationHandler.END

    db.update_task_deadline(task_id, user_id, raw_text)

    async def _reply(text, reply_markup=None):
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    return await _show_tasks(_reply, user_id, project_id, header=f"📅 Deadline for task #{task_id} set to {raw_text}.\n\n")


# ══════════════════════════════════════════════════════════════════════════════
# /project  flow
# ══════════════════════════════════════════════════════════════════════════════

async def project_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = await _resolve_chat_context(update, context)
    if result is None:
        return ConversationHandler.END
    user_id, chat_id = result
    await _send_project_picker(update, user_id, "📂 *Your projects — pick one:*", chat_id=chat_id)
    return PROJECT_PICK


async def project_picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)

    if query.data == "proj_new":
        context.user_data["after_newproject"] = "project"
        await query.edit_message_text("🗂 *New project name:*", parse_mode="Markdown")
        return NEWPROJECT_AWAIT_NAME

    project_id = int(query.data.split("_")[1])
    project    = db.get_project(project_id, user_id)
    context.user_data[_PROJECT_ID] = project_id

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 View latest notes",       callback_data="paction_notes")],
        [InlineKeyboardButton("✅ View tasks",              callback_data="paction_tasks")],
        [InlineKeyboardButton("📝 Add note",                callback_data="paction_addnote")],
        [InlineKeyboardButton("➕ Add task",                callback_data="paction_addtask")],
        [InlineKeyboardButton("🔄 Convert notes to tasks",  callback_data="paction_convert")],
    ])
    await query.edit_message_text(
        f"📂 *{project['name']}* — what do you want to do?",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return PROJECT_ACTION


async def project_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    await query.answer()
    user_id    = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)
    project_id = context.user_data[_PROJECT_ID]
    project    = db.get_project(project_id, user_id)
    action     = query.data

    if action == "paction_notes":
        notes = db.get_notes(user_id, project_id)
        if not notes:
            await query.edit_message_text(f"📭 No notes in *{project['name']}* yet.", parse_mode="Markdown")
            return ConversationHandler.END

        text = f"📋 *Latest notes — {project['name']}:*\n\n"
        kb   = []
        for n in notes:
            tags = f"  🏷 {n['tags']}" if n.get("tags") else ""
            ts   = n["created_at"][:16]
            text += f"• {n['refined_text']}\n  _{ts}{tags}_\n\n"
            kb.append([InlineKeyboardButton(
                f"👁 Raw note #{n['id']}", callback_data=f"rawnote_{n['id']}"
            )])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    if action == "paction_tasks":
        async def _edit(text, reply_markup=None):
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        return await _show_tasks(_edit, user_id, project_id)

    if action == "paction_addnote":
        await query.edit_message_text(
            f"📝 *Add note to {project['name']}:*\n_Hashtags like #vendor will be saved as tags._",
            parse_mode="Markdown",
        )
        return NOTE_AWAIT_TEXT

    if action == "paction_addtask":
        await query.edit_message_text(
            f"✏️ *New task for {project['name']}:*\n_Hashtags like #design will be saved as tags._",
            parse_mode="Markdown",
        )
        return TASK_AWAIT_TEXT

    if action == "paction_convert":
        notes = db.get_notes(user_id, project_id, limit=10)
        if not notes:
            await query.edit_message_text(
                f"📭 No notes in *{project['name']}* yet. Add some with /note first!",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        context.user_data[_NOTE_IDS]     = []
        context.user_data["notes_cache"] = {n["id"]: n for n in notes}
        kb = [
            [InlineKeyboardButton(
                f"⬜ {n['refined_text'][:50]}", callback_data=f"picknote_{n['id']}"
            )]
            for n in notes
        ]
        kb.append([InlineKeyboardButton("✅ Convert selected notes", callback_data="picknote_done")])
        await query.edit_message_text(
            "📋 *Select notes to convert:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return TASK_PICK_NOTES

    return ConversationHandler.END


# ── Show raw note callback ─────────────────────────────────────────────────────

async def show_raw_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    note_id = int(query.data.split("_")[1])
    user_id = context.user_data.get(_CHAT_OWNER_ID, query.from_user.id)
    note    = db.get_note(note_id, user_id)
    if not note:
        await query.answer("Note not found.", show_alert=True)
        return
    await query.message.reply_text(
        f"📝 *Raw note #{note_id}:*\n\n{note['raw_text']}",
        parse_mode="Markdown",
    )


# ── New-project mid-flow ───────────────────────────────────────────────────────

async def newproject_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get(_CHAT_OWNER_ID, update.effective_user.id)
    name    = update.message.text.strip()
    after   = context.user_data.pop("after_newproject", None)

    project_id = db.create_project(user_id, name)
    context.user_data[_PROJECT_ID] = project_id
    await update.message.reply_text(f"🎉 Project *{name}* created!", parse_mode="Markdown")

    if after == "note":
        await update.message.reply_text(
            f"✏️ *Type your note for {name}:*\n_Hashtags like #vendor will be saved as tags._",
            parse_mode="Markdown",
        )
        return NOTE_AWAIT_TEXT

    if after == "task":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Write a new task",    callback_data="taskmode_new")],
            [InlineKeyboardButton("🔄 Generate from notes", callback_data="taskmode_convert")],
            [InlineKeyboardButton("📋 View existing tasks", callback_data="taskmode_view")],
        ])
        await update.message.reply_text(
            f"📂 *{name}* — what would you like to do?",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return TASK_PICK_MODE

    if after == "project":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View latest notes",      callback_data="paction_notes")],
            [InlineKeyboardButton("✅ View tasks",             callback_data="paction_tasks")],
            [InlineKeyboardButton("📝 Add note",               callback_data="paction_addnote")],
            [InlineKeyboardButton("➕ Add task",               callback_data="paction_addtask")],
            [InlineKeyboardButton("🔄 Convert notes to tasks", callback_data="paction_convert")],
        ])
        await update.message.reply_text(
            f"📂 *{name}* — what do you want to do?",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return PROJECT_ACTION

    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# /chatprojects  flow  (groups only, admins only)
# ══════════════════════════════════════════════════════════════════════════════

async def chatprojects_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text(
            "ℹ️ /chatprojects only works in group chats.\n"
            "In private chats all your projects are always accessible."
        )
        return ConversationHandler.END

    if not await _is_chat_admin(update, context):
        await update.message.reply_text("⛔ Only group admins can configure chat projects.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    chat_id = chat.id

    db.register_chat(chat_id, chat.type, chat.title or "", user_id)

    projects = db.get_projects(user_id)
    if not projects:
        await update.message.reply_text(
            "You don't have any projects yet.\n"
            "Create one in a private chat with /project first, then come back here."
        )
        return ConversationHandler.END

    current = {p["id"] for p in db.get_chat_projects(chat_id)}
    context.user_data["cp_owner_id"] = user_id
    context.user_data["cp_selected"] = current

    kb = _build_chatprojects_kb(projects, current)
    await update.message.reply_text(
        "📂 *Select projects accessible in this chat:*\n_Tap to toggle, then Save._",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return CHATPROJECTS_PICK_ACTION


def _build_chatprojects_kb(projects, selected_ids):
    kb = []
    for p in projects:
        checked = p["id"] in selected_ids
        kb.append([InlineKeyboardButton(
            f"{'☑️' if checked else '⬜'} {p['name']}",
            callback_data=f"cp_toggle_{p['id']}"
        )])
    kb.append([InlineKeyboardButton("💾 Save", callback_data="cp_save")])
    return InlineKeyboardMarkup(kb)


async def chatprojects_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "cp_save":
        await query.answer()
        chat_id  = update.effective_chat.id
        selected = context.user_data.get("cp_selected", set())
        db.set_chat_projects(chat_id, list(selected))
        db.mark_chat_setup_complete(chat_id)
        await query.edit_message_text(
            f"✅ *Chat projects updated!*\n{len(selected)} project(s) are now accessible in this chat.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Toggle a project
    await query.answer()
    project_id = int(query.data.split("_")[2])
    selected   = context.user_data.setdefault("cp_selected", set())
    if project_id in selected:
        selected.discard(project_id)
    else:
        selected.add(project_id)

    owner_id = context.user_data.get("cp_owner_id")
    projects = db.get_projects(owner_id)
    await query.edit_message_reply_markup(reply_markup=_build_chatprojects_kb(projects, selected))
    return CHATPROJECTS_PICK_ACTION


# ── Bot added to group ────────────────────────────────────────────────────────

async def on_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires when bot's status changes in a chat (e.g. added to a group)."""
    new_status = update.my_chat_member.new_chat_member.status
    if new_status not in ("member", "administrator"):
        return

    chat = update.effective_chat
    user = update.effective_user  # the user who added the bot

    if chat.type == "private":
        return  # handled by _resolve_chat_context on first command

    db.register_chat(chat.id, chat.type, chat.title or "", user.id)
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "👋 Hi! I'm your AI note-taker bot.\n\n"
                "An admin needs to run /chatprojects to choose which projects are accessible in this chat."
            ),
        )
    except Exception as e:
        logger.error(f"on_bot_added send error: {e}")


# ── Task/reminder callbacks (outside conversation) ────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data    = query.data

    if data.startswith("done_"):
        task_id = int(data.split("_")[1])
        db.complete_task(task_id, user_id)
        await query.edit_message_text(f"✅ Task #{task_id} marked as done — great work! 🎉")

    elif data.startswith("remind_"):
        task_id = int(data.split("_")[1])
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("In 1 hour",      callback_data=f"setremind_{task_id}_1h"),
                InlineKeyboardButton("In 3 hours",     callback_data=f"setremind_{task_id}_3h"),
            ],
            [
                InlineKeyboardButton("Tomorrow 9 AM",  callback_data=f"setremind_{task_id}_tomorrow"),
                InlineKeyboardButton("In 1 week",      callback_data=f"setremind_{task_id}_1w"),
            ],
        ])
        await query.edit_message_text(
            f"⏰ When should I remind you about task #{task_id}?",
            reply_markup=kb,
        )

    elif data.startswith("setremind_"):
        parts, now = data.split("_"), datetime.now()
        task_id = int(parts[1])
        when    = parts[2]
        remind_at = {
            "1h":       now + timedelta(hours=1),
            "3h":       now + timedelta(hours=3),
            "tomorrow": (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0),
            "1w":       now + timedelta(weeks=1),
        }.get(when, now + timedelta(hours=1))
        db.set_task_reminder(task_id, user_id, remind_at.strftime("%Y-%m-%d %H:%M:%S"))
        await query.edit_message_text(
            f"⏰ I'll remind you about task #{task_id} on "
            f"*{remind_at.strftime('%b %d at %H:%M')}* 👍",
            parse_mode="Markdown",
        )

    elif data.startswith("rawnote_"):
        await show_raw_note(update, context)


# ── Reminders job ─────────────────────────────────────────────────────────────

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    for task in db.get_due_reminders():
        try:
            desc = f"_{task['description']}_\n" if task.get("description") else ""
            await context.bot.send_message(
                chat_id=task["user_id"],
                text=(
                    f"⏰ *Reminder!*\n\n"
                    f"📂 {task['project_name']}\n"
                    f"📌 *{task['title']}*\n{desc}"
                    f"\n_Use /project to manage it._"
                ),
                parse_mode="Markdown",
            )
            db.mark_reminded(task["id"])
        except Exception as e:
            logger.error(f"Reminder error task {task['id']}: {e}")


async def check_deadline_reminders(context: ContextTypes.DEFAULT_TYPE):
    for task in db.get_approaching_deadlines():
        try:
            await context.bot.send_message(
                chat_id=task["user_id"],
                text=(
                    f"📅 *Deadline tomorrow!*\n\n"
                    f"📂 {task['project_name']}\n"
                    f"📌 *{task['title']}*\n"
                    f"🗓 Due: {task['deadline']}\n"
                    f"\n_Use /task to manage it._"
                ),
                parse_mode="Markdown",
            )
            db.mark_deadline_reminded(task["id"])
        except Exception as e:
            logger.error(f"Deadline reminder error task {task['id']}: {e}")


# ── Bot command registration ──────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",         "Welcome screen"),
        BotCommand("note",          "Capture a note under a project"),
        BotCommand("task",          "Create or convert tasks"),
        BotCommand("project",       "Browse and manage your projects"),
        BotCommand("chatprojects",  "Configure projects for this group (admins only)"),
        BotCommand("adduser",       "[Admin] Whitelist a user by ID"),
        BotCommand("removeuser",    "[Admin] Remove a user from the whitelist"),
        BotCommand("addchat",       "[Admin] Whitelist this chat"),
        BotCommand("removechat",    "[Admin] Remove this chat from the whitelist"),
        BotCommand("listaccess",    "[Admin] Show all whitelisted users and chats"),
    ])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    # Seed master admin into whitelist on every startup (safe — uses INSERT OR IGNORE)
    if ADMIN_USER_ID:
        db.add_to_whitelist("user", ADMIN_USER_ID, ADMIN_USER_ID)
        logger.info(f"Admin user {ADMIN_USER_ID} ensured in whitelist")
    else:
        logger.warning("ADMIN_USER_ID not set — bot is open to everyone!")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # /note conversation
    note_conv = ConversationHandler(
        entry_points=[CommandHandler("note", note_entry)],
        states={
            NOTE_PICK_PROJECT:     [CallbackQueryHandler(note_project_chosen, pattern=r"^proj_")],
            NOTE_AWAIT_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, note_receive_text)],
            NEWPROJECT_AWAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newproject_receive_name)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        allow_reentry=True,
    )

    # /task conversation
    task_conv = ConversationHandler(
        entry_points=[CommandHandler("task", task_entry)],
        states={
            TASK_PICK_PROJECT:     [CallbackQueryHandler(task_project_chosen, pattern=r"^proj_")],
            TASK_PICK_MODE:        [CallbackQueryHandler(task_mode_chosen, pattern=r"^taskmode_")],
            TASK_AWAIT_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, task_receive_text)],
            TASK_PICK_NOTES:       [CallbackQueryHandler(task_note_toggle, pattern=r"^picknote_")],
            TASK_REVIEW_SUGGESTED: [CallbackQueryHandler(task_suggested_toggle, pattern=r"^stask_")],
            TASK_VIEW_LIST:        [CallbackQueryHandler(task_view_handler, pattern=r"^tv_")],
            TASK_EDIT_CONTENT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, task_edit_content_handler)],
            TASK_EDIT_DEADLINE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, task_edit_deadline_handler)],
            NEWPROJECT_AWAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newproject_receive_name)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        allow_reentry=True,
    )

    # /project conversation
    project_conv = ConversationHandler(
        entry_points=[CommandHandler("project", project_entry)],
        states={
            PROJECT_PICK:          [CallbackQueryHandler(project_picked, pattern=r"^proj_")],
            PROJECT_ACTION:        [CallbackQueryHandler(project_action, pattern=r"^paction_")],
            NOTE_AWAIT_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, note_receive_text)],
            TASK_AWAIT_TEXT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, task_receive_text)],
            TASK_PICK_NOTES:       [CallbackQueryHandler(task_note_toggle, pattern=r"^picknote_")],
            TASK_REVIEW_SUGGESTED: [CallbackQueryHandler(task_suggested_toggle, pattern=r"^stask_")],
            TASK_VIEW_LIST:        [CallbackQueryHandler(task_view_handler, pattern=r"^tv_")],
            TASK_EDIT_CONTENT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, task_edit_content_handler)],
            TASK_EDIT_DEADLINE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, task_edit_deadline_handler)],
            NEWPROJECT_AWAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newproject_receive_name)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        allow_reentry=True,
    )

    # /chatprojects conversation
    chatproj_conv = ConversationHandler(
        entry_points=[CommandHandler("chatprojects", chatprojects_entry)],
        states={
            CHATPROJECTS_PICK_ACTION: [CallbackQueryHandler(chatprojects_toggle, pattern=r"^cp_")],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        allow_reentry=True,
    )

    # Access guard runs before every handler (group -1)
    app.add_handler(TypeHandler(Update, access_guard), group=-1)

    app.add_handler(CommandHandler("start", start))

    # Admin commands
    app.add_handler(CommandHandler("adduser",    cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("addchat",    cmd_addchat))
    app.add_handler(CommandHandler("removechat", cmd_removechat))
    app.add_handler(CommandHandler("listaccess", cmd_listaccess))

    app.add_handler(note_conv)
    app.add_handler(task_conv)
    app.add_handler(project_conv)
    app.add_handler(chatproj_conv)

    # Detect when bot is added to a group
    app.add_handler(ChatMemberHandler(on_bot_added, ChatMemberHandler.MY_CHAT_MEMBER))

    # Global callbacks not inside a conversation (done/remind/rawnote)
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.job_queue.run_repeating(check_reminders, interval=60, first=15)
    app.job_queue.run_repeating(check_deadline_reminders, interval=3600, first=30)

    logger.info("🤖 Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
