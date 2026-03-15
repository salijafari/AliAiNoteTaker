# 🤖 AI Productivity Telegram Bot

A personal note-taking and task management bot powered by Claude AI.
Supports multiple projects, reminders, deadlines, and natural language commands.
Works in private chats and group chats (whitelisted only).

---

## Features

- 📝 Save quick notes per project, refined by Claude AI
- 🤖 AI converts notes into actionable tasks
- ✅ Mark tasks complete, edit content, set deadlines
- ⏰ Deadline reminders (1 day before) and custom reminders
- 📂 Multiple projects per user
- 👥 Group chat support (admin controls which groups can access)
- 🔒 Whitelist-based access control — only allowed users/chats can use the bot

---

## Commands

### Regular commands
| Command | What it does |
|---------|-------------|
| `/start` | Welcome screen |
| `/note` | Capture a note under a project |
| `/task` | Create tasks, generate from notes, or view existing |
| `/project` | Browse and manage your projects |
| `/ideas` | View recent ideas for the active project |
| `/journal` | View recent journal entries for the active project |
| `/references` | View saved links for the active project |
| `/search <query>` | Search across notes, tasks, ideas, journal, and references |
| `/digest` | Get today's activity digest (also sent automatically at 8 PM UTC) |
| `/chatprojects` | (Group admins) Choose which projects are accessible in this group |

### Send anything — the bot classifies it automatically
Just send a plain text message, a voice note, a photo, or a URL — the bot will:
- **Voice/audio** → transcribed with OpenAI Whisper, then classified
- **Photo/image** → text extracted with Claude Vision, then classified
- **URL** → page title and description fetched and saved as a reference
- **Plain text** → classified by Claude as a note, task, idea, or journal entry

After every save, tap the inline buttons to reclassify or move to a different category.
If the item has a deadline, a **Add to Google Calendar** button appears automatically.

### Admin-only commands
| Command | What it does |
|---------|-------------|
| `/adduser <user_id>` | Whitelist a user by their Telegram numeric ID |
| `/removeuser <user_id>` | Remove a user from the whitelist |
| `/addchat` | Whitelist the current group/channel (run inside the group) |
| `/removechat` | Remove the current group/channel from the whitelist |
| `/listaccess` | Show all whitelisted users and chats |

---

## Step-by-Step Setup

### Step 1 — Create your Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send: `/newbot`
3. Choose a name and a username ending in `bot`
4. BotFather gives you a **token** — copy it, keep it safe!

---

### Step 2 — Get your Anthropic API key

1. Go to https://console.anthropic.com
2. Sign in or create an account
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`)

---

### Step 3 — Find your Telegram user ID (for ADMIN_USER_ID)

1. Open Telegram and search for **@userinfobot**
2. Send any message to it
3. It replies instantly with your numeric user ID (e.g. `123456789`)
4. Save this number — you'll need it in Step 4

This ID becomes the **master admin** of your bot. Only this account can run admin commands.

---

### Step 4 — Put your code on GitHub

1. Go to https://github.com and create a free account if needed
2. Create a new **private** repository named `productivity-bot`
3. Upload all bot files:
   - `main.py`, `database.py`, `claude_ai.py`
   - `requirements.txt`, `Procfile`, `railway.toml`
   - `.env.example` (safe to commit — no real secrets in it)
4. Do **NOT** upload your `.env` file — that stays private!

---

### Step 5 — Deploy on Railway

1. Go to https://railway.app and sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo** → select your repo

**Add environment variables:**

3. In Railway, go to your project → **Variables** tab
4. Add these variables:
   - `TELEGRAM_BOT_TOKEN` = (your BotFather token)
   - `ANTHROPIC_API_KEY` = (your Anthropic key)
   - `ADMIN_USER_ID` = (your numeric Telegram user ID from Step 3)
   - `OPENAI_API_KEY` = (your OpenAI key — required for voice transcription, optional otherwise)

**Add a persistent volume (IMPORTANT — prevents data loss on redeploy):**

5. Go to your service → **Volumes** tab → **Add Volume**
6. Set **Mount Path** to `/data`
7. Click **Add**

8. Click **Deploy** — Railway installs packages and starts your bot!

---

### Step 6 — First run

1. Open Telegram and search for your bot by username
2. Send `/start` — the bot responds (you're the admin, always allowed)
3. To give another person access: `/adduser 987654321` (their user ID)
4. To allow a group: add the bot to the group, then run `/addchat` inside it

---

## Adding the bot to a group

1. Open the group in Telegram
2. Tap the group name → **Add Members** → search for your bot
3. Inside the group, run `/addchat` (you must be the admin)
4. Then run `/chatprojects` to choose which of your projects are accessible in that group
5. Members of the group can now use `/note`, `/task`, `/project` inside the group

---

## Access control

- The bot silently ignores (or sends "Access denied" to) any user or chat not on the whitelist
- `ADMIN_USER_ID` is the master admin — permanently whitelisted, can never be removed
- You can whitelist individual users (`/adduser`) or entire chats (`/addchat`)
- `/listaccess` shows everything currently whitelisted

---

## File Structure

```
productivity-bot/
├── main.py          ← Bot logic + all commands
├── database.py      ← SQLite: notes, tasks, projects, whitelist
├── claude_ai.py     ← Claude AI integration
├── requirements.txt ← Python packages
├── Procfile         ← Railway start command
├── railway.toml     ← Railway deploy config
└── .env.example     ← Template for environment variables
```

---

## Notes

- Data is stored in SQLite at `/data/bot_data.db` (Railway persistent volume)
- Without the Railway volume, data resets on every redeploy — set it up in Step 5
- Deadline reminders fire once per hour; custom reminders check every 60 seconds
- You can have unlimited projects, notes, and tasks
