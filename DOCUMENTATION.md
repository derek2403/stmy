# Superteam MY Community Bot

A Telegram bot that manages member verification, onboarding, and admin tools for the Superteam MY community group.

**Tech Stack:** Python, python-telegram-bot v21+, OpenAI GPT-4o-mini, JSON file database

---

## Table of Contents

- [Features Overview](#features-overview)
- [Setup & Configuration](#setup--configuration)
- [Feature Details](#feature-details)
  - [1. Member Verification & Onboarding](#1-member-verification--onboarding)
  - [2. Verification Enforcement](#2-verification-enforcement)
  - [3. Intro Generation](#3-intro-generation)
  - [4. Admin Portal](#4-admin-portal)
  - [5. Link Safeguard](#5-link-safeguard)
  - [6. Contact Query Auto-Reply](#6-contact-query-auto-reply)
  - [7. Registration Lookup](#7-registration-lookup)
- [Commands Reference](#commands-reference)
- [Architecture](#architecture)

---

## Features Overview

| Feature | Description |
|---------|-------------|
| Member Verification | 5-question Q&A via DM with LLM validation |
| Verification Enforcement | Auto-deletes messages from unverified users |
| Intro Generation | AI-generated structured welcome intro posted to Intros topic |
| Admin Portal | Password-protected admin menu with Summary, Stats, Members analytics |
| Link Safeguard | Auto-warns on links, forwards to admin with delete button |
| Contact Auto-Reply | Detects "who to contact" questions, replies with PIC handles |
| Registration Lookup | `/verify` command to check registration status by handle |

---

## Setup & Configuration

### Environment Variables (`.env`)

```env
TELEGRAM_BOT_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_key
GROUP_CHAT_ID=your_group_chat_id
VERIFY_TOPIC_ID=11
INTROS_TOPIC_ID=8
ADMIN_TOPIC_ID=37
ADMIN_PASSWORD=your_admin_password
PIC_HANDLES=@handle1, @handle2
```

### Prerequisites

- Bot must be added as **admin** in the group with:
  - Delete Messages
  - Restrict Members (optional, not relied on)
  - Manage Topics
- Group should be a **supergroup with forum/topics enabled**
- **Send Messages and Send Media should be ENABLED** at the group level — the bot enforces verification by deleting messages from unverified users, not by restricting permissions

### Discovering Topic IDs

Run `/setup` inside each topic to get its thread ID:

```
Chat ID: -100xxxxxxxxxx
Topic thread ID: 11
```

---

## Feature Details

### 1. Member Verification & Onboarding

**Flow:**

1. New member joins the group
2. Bot detects the join and posts a welcome message in the **Verify** topic with a "Start Verification" button
3. User clicks the button, which deep-links to the bot's DM
4. Bot asks 5 questions:
   - What's your name?
   - Who are you & what do you do?
   - Where are you based?
   - One fun fact about you!
   - How are you looking to contribute to Superteam MY?
5. Answers must be at least **10 characters** (except name) to ensure quality
6. Answers are validated by LLM — gibberish, trolling, or dismissive responses are rejected and the user is asked to redo those specific questions
7. On success: an AI-generated intro is posted to the **Intros** topic, the user is saved to `members.json`, and all verification prompts are cleaned up

**Returning Members:** If a user who's already in `members.json` rejoins, the bot recognizes them and sends a "Welcome back" message — no re-verification needed.

---

### 2. Verification Enforcement

Unverified users **cannot send messages** in any topic (except the Verify topic). If they try:

1. Their message is immediately deleted
2. A nag message appears with a "Verify Now" button (max once per 60 seconds per user)
3. All nag messages are automatically deleted once the user completes verification

**Exceptions:** Bot messages, group admins/creators, and messages in the Verify topic are never filtered.

---

### 3. Intro Generation

After verification, the bot generates a structured welcome post using OpenAI:

```
Hey everyone! Let's welcome Sarah 👋

She's a full-stack developer passionate about building DeFi tools
and has been in the Web3 space for 3 years.

📍 Based in Kuala Lumpur

🧑‍🎓 Fun fact: She once built an entire NFT marketplace in a weekend hackathon!

🤝 Looking to contribute by:
• Mentoring new developers in the community
• Building open-source tooling for Solana projects
• Helping with technical content and workshops

Drop her a message — she'd love to connect! 🚀
```

The tone, emoji choices, and wording vary each time to keep intros feeling fresh and human.

---

### 4. Admin Portal

Access via `/admin` in DM with the bot. Password-protected.

**Menu Options:**

| Button | Function |
|--------|----------|
| 📊 Summary | AI summary of the last 100 messages from the General topic |
| 📈 Stats | Top 10 most active members by message count with leaderboard |
| 👥 Members | Ask any question about the member base (e.g., "how many developers?") |
| 🚪 Logout | End admin session |

**Stats Leaderboard Example:**
```
📈 Activity Leaderboard
─────────────────────────

🥇 @alice — 42 msgs
🥈 @bob — 31 msgs
🥉 @charlie — 28 msgs
  4. @dave — 15 msgs

📊 Total: 234 messages tracked
```

**Members Query:** Uses AI to analyze the member database. Ask natural questions like:
- "How many members are based in KL?"
- "List all developers"
- "What are the most common professions?"

> **Note:** The bot only tracks messages received while it's running. Message history is kept in memory (capped at 500 messages) and resets on restart.

---

### 5. Link Safeguard

When anyone posts a link in the group:

1. **Auto-reply** in the same topic:
   ```
   🛡️ Link Detected — Stay Safe!
   ━━━━━━━━━━━━━━━━━━━━
   🔍 Verify the link before clicking
   🔑 Never share your private keys
   🚫 Watch out for scams & phishing
   ```

2. **Forwards to all authenticated admins** via DM with a "Delete Message" button
3. Admin can one-click delete the suspicious message from the group

Messages in the Admin topic are excluded from link safeguard.

---

### 6. Contact Query Auto-Reply

When someone asks a question like "Who should I contact about partnerships?" or "Who's the admin here?", the bot automatically replies with the configured points of contact.

**How it works:**
1. Pre-filters messages for contact-related keywords (who, contact, reach, poc, lead, admin, etc.)
2. Confirms with LLM that the message is actually asking about a contact
3. Replies with the PIC handles from config

---

### 7. Registration Lookup

Users can check their registration status by typing `/verify` in the Verify topic:

1. Bot asks for their Telegram handle
2. Looks up the handle in `members.json` (case-insensitive)
3. If found: shows name, about, and verification date
4. If not found: shows a registration button

---

## Commands Reference

| Command | Where | Who | Description |
|---------|-------|-----|-------------|
| `/start` | DM | Anyone | Begin verification (via deep link) |
| `/verify` | Verify topic | Anyone | Check registration status by handle |
| `/admin` | DM | Anyone | Enter admin portal (password required) |
| `/logout` | DM | Admins | End admin session |
| `/summary` | DM | Admins | Summarize recent General topic messages |
| `/stats` | DM | Admins | Show activity leaderboard |
| `/members` | DM | Admins | Query member database with AI |
| `/setup` | Group | Admins | Discover chat ID and topic thread IDs |

---

## Architecture

### Files

```
stmy/
├── bot.py            # Main bot logic, handlers, flows
├── llm_service.py    # OpenAI integration (validation, intros, summaries)
├── db.py             # JSON database for members
├── config.py         # Environment configuration
├── members.json      # Member data (auto-created)
└── .env              # Secrets (not committed)
```

### Handler Priority (Group Numbers)

Messages flow through handler groups in order. Lower numbers run first.

| Group | Type | Handler | Purpose |
|-------|------|---------|---------|
| 0 | Default | ChatMemberHandler, Commands, Callbacks | Join detection, commands, button clicks |
| 1 | Group | `enforce_verification` | Delete unverified messages + stop propagation |
| 2 | Group | `handle_link_safeguard` | Link safety warnings |
| 3 | Group | `track_message` | Buffer messages for analytics |
| 4 | Group | `handle_contact_query` | Contact auto-reply |
| 5 | Group | `handle_handle_input` | /verify handle lookup |
| 0-3 | DM | Admin password, members query, redo, Q&A | DM message routing |

When `enforce_verification` blocks an unverified user, it raises `ApplicationHandlerStop` — handlers in groups 2-5 never see the message.

### Data Storage

- **Persistent:** `members.json` — member records with user_id, handle, name, about, location, fun_fact, contribution, verified_at
- **In-Memory:** verification state, admin sessions, message buffer (500 max), nag cooldowns

### LLM Usage

| Function | Model | Temperature | Purpose |
|----------|-------|-------------|---------|
| `validate_answers` | gpt-4o-mini | 0.1 | Check answer legitimacy |
| `generate_intro` | gpt-4o-mini | 0.9 | Create varied welcome intros |
| `summarize_messages` | gpt-4o-mini | 0.3 | Summarize chat discussions |
| `is_contact_query` | gpt-4o-mini | 0.0 | Detect contact questions |
| `answer_members_question` | gpt-4o-mini | 0.3 | Answer admin member queries |
