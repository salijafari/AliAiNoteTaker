# 🤖 AI Productivity Telegram Bot

A personal note-taking and task management bot powered by Claude AI.
Supports multiple projects, reminders, and natural language commands.

---

## Features

- 📝 Save quick notes per project
- 🤖 AI converts notes into actionable tasks (powered by Claude)
- ✅ Mark tasks complete with one tap
- ⏰ Set reminders (1hr / 3hr / tomorrow / 1 week)
- 📂 Multiple projects (YVR, Evalyze, Personal, etc.)
- 💬 Natural language — just talk to the bot normally

---

## Commands

| Command | What it does |
|---------|-------------|
| `/start` | Welcome screen + help |
| `/note your text` | Save a note to active project |
| `/notes` | View recent notes |
| `/tasks` | View pending tasks (with Done + Remind buttons) |
| `/projects` | List projects + switch between them |
| `/newproject Name` | Create a new project |
| `/convert` | Let Claude turn your notes into tasks |

Or just chat naturally — Claude understands plain English!

---

## Step-by-Step Setup

### Step 1 — Create your Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send: `/newbot`
3. Choose a name (e.g. "Ali's Productivity Bot")
4. Choose a username ending in `bot` (e.g. `ali_productivity_bot`)
5. BotFather gives you a **token** — copy it, keep it safe!

---

### Step 2 — Get your Anthropic API key

1. Go to https://console.anthropic.com
2. Sign in or create an account
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`)

---

### Step 3 — Put your code on GitHub

1. Go to https://github.com and create a free account if you don't have one
2. Click the **+** button → **New repository**
3. Name it `productivity-bot`, set it to **Private**, click Create
4. On your computer, create a folder called `productivity-bot`
5. Copy all the bot files into it:
   - `main.py`
   - `database.py`
   - `claude_ai.py`
   - `requirements.txt`
   - `Procfile`
   - `.env.example`
6. Do NOT add the `.env` file — that stays private!
7. Upload to GitHub:
   - On the repository page, click **uploading an existing file**
   - Drag and drop the files
   - Click **Commit changes**

---

### Step 4 — Deploy on Railway

1. Go to https://railway.app and sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `productivity-bot` repository
4. Railway will detect it automatically

**Add your secret keys:**
5. In Railway, go to your project → **Variables** tab
6. Add these two variables:
   - `TELEGRAM_BOT_TOKEN` = (paste your BotFather token)
   - `ANTHROPIC_API_KEY` = (paste your Anthropic key)

**Set the start command:**
7. Go to **Settings** → **Deploy** section
8. Set the **Start Command** to: `python main.py`

9. Click **Deploy** — Railway will install packages and start your bot!

---

### Step 5 — Test it!

1. Open Telegram and search for your bot by username
2. Send `/start`
3. Type your first project name (e.g. "YVR")
4. Try: `/note follow up with vendor about voucher system`
5. Try: `/convert` — Claude will turn your notes into tasks
6. Try: `/tasks` — tap the Remind button on any task

---

## File Structure

```
productivity-bot/
├── main.py          ← Bot logic + all commands
├── database.py      ← Saves notes/tasks to SQLite
├── claude_ai.py     ← Talks to Claude API
├── requirements.txt ← Python packages needed
├── Procfile         ← Tells Railway how to start
└── .env.example     ← Template for your secret keys
```

---

## Notes

- Your data is stored in a SQLite file (`bot_data.db`) on Railway's server
- Railway's free tier may sleep after inactivity — upgrade to the $5/mo Hobby plan for always-on
- The bot checks for due reminders every 60 seconds
- You can have as many projects as you need
