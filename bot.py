import logging
import time
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMemberUpdated,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    ChatMemberHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from collections import Counter
from datetime import datetime, timezone

import config
from llm_service import validate_answers, generate_intro, summarize_messages, is_contact_query, answer_members_question
from db import add_member, get_member, get_member_by_handle, get_all_members

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# In-memory verification state
# {user_id: {"step": int, "answers": dict, "verify_msg_id": int, "chat_id": int}}
verification_state: dict[int, dict] = {}

# In-memory message buffer for /summary and /stats (capped at 500)
MAX_BUFFER = 500
message_buffer: list[dict] = []

# Authenticated admin user IDs and pending auth
authenticated_admins: set[int] = set()
pending_admin_auth: set[int] = set()  # users who typed /admin and need to enter password

# Users who typed /verify in the verify topic and need to enter their handle
pending_handle_check: set[int] = set()

# Admins who typed /members and need to type their question
pending_members_query: set[int] = set()


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    """Return the inline keyboard for the admin portal."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Summary", callback_data="admin_summary"),
         InlineKeyboardButton("📈 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Members", callback_data="admin_members")],
        [InlineKeyboardButton("🚪 Logout", callback_data="admin_logout")],
    ])

# Minimum character count for verification answers
MIN_ANSWER_LENGTH = 10

# Cooldown for verification nag messages (seconds)
VERIFICATION_NAG_COOLDOWN = 60
verification_nag_cooldown: dict[int, float] = {}

# Track nag message IDs so we can delete them after verification
# {user_id: [(chat_id, message_id, thread_id), ...]}
verification_nag_messages: dict[int, list[tuple[int, int, int | None]]] = {}

QUESTIONS = [
    ("name", "What's your name?"),
    ("about", "Who are you & what do you do?"),
    ("location", "Where are you based?"),
    ("fun_fact", "One fun fact about you!"),
    ("contribution", "How are you looking to contribute to Superteam MY?"),
]

FIELD_LABELS = {
    "name": "name",
    "about": "who you are & what you do",
    "location": "location",
    "fun_fact": "fun fact",
    "contribution": "how you want to contribute",
}


async def greet_new_member(user_id: int, display_name: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Shared logic for handling a new member join."""
    existing = get_member(user_id)
    if existing:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=config.VERIFY_TOPIC_ID,
            text=(
                f"👋 Welcome back, {existing['name']}!\n\n"
                "✅ You're already a verified member. Enjoy the community!"
            ),
        )
        logger.info(f"Returning member {display_name} ({user_id}) — already verified.")
        return

    # Send welcome message in verify topic with inline button
    bot_username = (await context.bot.get_me()).username
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✨ Start Verification",
                    url=f"https://t.me/{bot_username}?start=verify_{chat_id}",
                )
            ]
        ]
    )

    msg = await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=config.VERIFY_TOPIC_ID,
        text=(
            f"👋 Hey {display_name}! Welcome to Superteam MY!\n\n"
            "🔒 To unlock full access, complete a quick verification.\n"
            "It only takes a minute — tap the button below to get started!"
        ),
        reply_markup=keyboard,
    )

    # Store state so we can delete this message later
    verification_state[user_id] = {
        "step": -1,  # -1 = hasn't started yet
        "answers": {},
        "verify_msg_id": msg.message_id,
        "chat_id": chat_id,
    }

    logger.info(f"New member {display_name} ({user_id}) joined, prompted to verify.")


async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detect new members via chat_member update (requires bot to be admin)."""
    member_update = update.chat_member
    old = member_update.old_chat_member
    new = member_update.new_chat_member

    if old.status not in ("left", "banned") or new.status not in ("member", "restricted"):
        return

    new_user = new.user
    if new_user.is_bot:
        return

    await greet_new_member(new_user.id, new_user.full_name, member_update.chat.id, context)


async def handle_new_member_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback: detect new members via service message (works without admin)."""
    if not update.message or not update.message.new_chat_members:
        return

    chat_id = update.effective_chat.id
    for new_user in update.message.new_chat_members:
        if new_user.is_bot:
            continue
        await greet_new_member(new_user.id, new_user.full_name, chat_id, context)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command in DM — begins verification if deep-linked."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    args = context.args

    # Deep link: /start verify_{chat_id}
    if args and args[0].startswith("verify_"):
        try:
            chat_id = int(args[0].replace("verify_", ""))
        except ValueError:
            await update.message.reply_text("❌ Invalid verification link.")
            return

        # Check if user is already registered in members.json
        existing = get_member(user_id)
        if existing:
            await update.message.reply_text(
                f"✅ Hey {existing['name']}! You're already a verified member.\n\n"
                "No need to go through verification again — you have full access. Welcome back! 🎉"
            )
            # Clean up verification state if any
            verification_state.pop(user_id, None)
            return

        # Check if user has a pending verification
        if user_id not in verification_state:
            # They might have clicked the link but weren't tracked (e.g. bot restarted)
            verification_state[user_id] = {
                "step": -1,
                "answers": {},
                "verify_msg_id": None,
                "chat_id": chat_id,
            }

        state = verification_state[user_id]

        if state["step"] >= len(QUESTIONS):
            await update.message.reply_text("✅ You've already completed verification!")
            return

        # Start asking questions
        state["step"] = 0
        state["chat_id"] = chat_id
        await update.message.reply_text(
            "🚀 Let's get you verified! I'll ask you a few quick questions.\n\n"
            f"📝 *Question 1/{len(QUESTIONS)}:* {QUESTIONS[0][1]}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "👋 Hey there! I'm the Superteam MY community bot.\n\n"
            "If you've just joined the group, head over to the Verify topic and click the verification button to get started!"
        )


async def handle_dm_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle answers in DM during verification."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id

    if user_id not in verification_state:
        return

    state = verification_state[user_id]

    if state["step"] < 0 or state["step"] >= len(QUESTIONS):
        return

    answer_text = update.message.text.strip()

    # Enforce minimum character length (skip for name)
    field_name = QUESTIONS[state["step"]][0]
    if field_name != "name" and len(answer_text) < MIN_ANSWER_LENGTH:
        label = FIELD_LABELS.get(field_name, "this")
        await update.message.reply_text(
            f"✏️ That's a bit too short! Please provide more detail for *{label}* "
            f"(at least {MIN_ANSWER_LENGTH} characters).",
            parse_mode="Markdown",
        )
        return

    # Record the answer
    state["answers"][field_name] = answer_text

    # Move to next question
    state["step"] += 1

    if state["step"] < len(QUESTIONS):
        q_num = state["step"] + 1
        question_text = QUESTIONS[state["step"]][1]
        await update.message.reply_text(
            f"📝 *Question {q_num}/{len(QUESTIONS)}:* {question_text}",
            parse_mode="Markdown",
        )
    else:
        # All questions answered — validate
        await update.message.reply_text("⏳ Thanks! Let me verify your answers...")
        await process_verification(update, context, user_id)


async def process_verification(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int
):
    """Validate answers with LLM and finalize verification."""
    state = verification_state[user_id]
    answers = state["answers"]
    chat_id = state["chat_id"]

    is_valid, invalid_fields = await validate_answers(answers)

    if not is_valid and invalid_fields:
        # Ask user to redo invalid answers
        field_names = ", ".join(FIELD_LABELS.get(f, f) for f in invalid_fields)
        await update.message.reply_text(
            f"⚠️ Hmm, some of your answers need a redo: *{field_names}*\n\n"
            "No worries — just give it another shot!\n\n"
            f"📝 *{QUESTIONS[next(i for i, (k, _) in enumerate(QUESTIONS) if k == invalid_fields[0])][1]}*",
            parse_mode="Markdown",
        )

        # Set step to the first invalid field so we re-collect from there
        state["redo_fields"] = invalid_fields
        state["redo_index"] = 0
        state["step"] = -2  # special redo mode
        return

    # Generate intro
    intro_text = await generate_intro(answers)

    # Post intro to intros topic
    await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=config.INTROS_TOPIC_ID,
        text=intro_text,
    )

    # Save to JSON database
    handle = ""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        handle = f"@{member.user.username}" if member.user.username else member.user.full_name
    except Exception:
        handle = answers.get("name", "unknown")
    add_member(
        user_id=user_id,
        handle=handle,
        name=answers.get("name", ""),
        about=answers.get("about", ""),
        location=answers.get("location", ""),
        fun_fact=answers.get("fun_fact", ""),
        contribution=answers.get("contribution", ""),
    )

    # Delete verify topic message
    if state.get("verify_msg_id"):
        try:
            await context.bot.delete_message(
                chat_id=chat_id, message_id=state["verify_msg_id"]
            )
        except Exception:
            pass  # Message may already be deleted

    # Confirm in DM
    await update.message.reply_text(
        "🎉 You're all set! Welcome to Superteam MY!\n\n"
        "📝 Your intro has been posted in the Intros topic\n\n"
        "Jump in and say hi to the community!"
    )

    # Delete all nag messages for this user
    for nag_chat_id, nag_msg_id, _ in verification_nag_messages.pop(user_id, []):
        try:
            await context.bot.delete_message(chat_id=nag_chat_id, message_id=nag_msg_id)
        except Exception:
            pass
    verification_nag_cooldown.pop(user_id, None)

    # Cleanup state
    del verification_state[user_id]
    logger.info(f"User {user_id} verified successfully.")


async def handle_redo_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle re-submitted answers during redo mode."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    if user_id not in verification_state:
        return

    state = verification_state[user_id]
    if state["step"] != -2:  # not in redo mode
        return

    redo_fields = state["redo_fields"]
    redo_index = state["redo_index"]

    # Record the corrected answer
    current_field = redo_fields[redo_index]
    answer_text = update.message.text.strip()

    # Enforce minimum character length (skip for name)
    if current_field != "name" and len(answer_text) < MIN_ANSWER_LENGTH:
        label = FIELD_LABELS.get(current_field, "this")
        await update.message.reply_text(
            f"✏️ That's a bit too short! Please provide more detail for *{label}* "
            f"(at least {MIN_ANSWER_LENGTH} characters).",
            parse_mode="Markdown",
        )
        return

    state["answers"][current_field] = answer_text
    state["redo_index"] += 1

    if state["redo_index"] < len(redo_fields):
        # Ask for next invalid field
        next_field = redo_fields[state["redo_index"]]
        q_index = next(i for i, (k, _) in enumerate(QUESTIONS) if k == next_field)
        await update.message.reply_text(
            f"📝 *{QUESTIONS[q_index][1]}*",
            parse_mode="Markdown",
        )
    else:
        # All redone — re-validate
        state["step"] = len(QUESTIONS)  # mark as complete to prevent re-entry
        await update.message.reply_text("⏳ Thanks! Let me verify again...")
        await process_verification(update, context, user_id)


async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track all group text messages for /summary and /stats."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not update.message or not update.message.text:
        return

    message_buffer.append({
        "user_id": update.effective_user.id,
        "display_name": update.effective_user.full_name,
        "username": update.effective_user.username or "",
        "text": update.message.text,
        "thread_id": update.message.message_thread_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Cap the buffer
    if len(message_buffer) > MAX_BUFFER:
        del message_buffer[: len(message_buffer) - MAX_BUFFER]


async def handle_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command in DM — start admin authentication."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please use this command in a DM with me.")
        return

    user_id = update.effective_user.id

    if user_id in authenticated_admins:
        await update.message.reply_text(
            "🔐 You're already logged in!\n\nWhat would you like to do?",
            reply_markup=admin_menu_keyboard(),
        )
        return

    pending_admin_auth.add(user_id)
    await update.message.reply_text("🔑 Enter the admin password:")


async def handle_admin_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle password input for admin authentication."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    if user_id not in pending_admin_auth:
        return

    pending_admin_auth.discard(user_id)

    if update.message.text == config.ADMIN_PASSWORD:
        authenticated_admins.add(user_id)
        logger.info(f"Admin authenticated: {update.effective_user.full_name} ({user_id})")
        await update.message.reply_text(
            "✅ Authenticated! Welcome to the Admin Portal.\n\nWhat would you like to do?",
            reply_markup=admin_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ Wrong password. Try again with /admin")


async def handle_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logout command — end admin session."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    if user_id in authenticated_admins:
        authenticated_admins.discard(user_id)
        await update.message.reply_text("👋 Logged out. Use /admin to log in again.")
    else:
        await update.message.reply_text("You're not logged in. Use /admin to get started.")


async def handle_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command (DM): summarize last 100 messages from general topic."""
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in authenticated_admins:
        await update.message.reply_text("🔒 You need to authenticate first. Use /admin")
        return

    # General topic messages have thread_id=None (or 1 in some groups)
    general_msgs = [m for m in message_buffer if m["thread_id"] is None]
    if not general_msgs:
        await update.message.reply_text("📭 No messages tracked from General yet.\n\nThe bot only tracks messages received since it started running.")
        return

    last_100 = general_msgs[-100:]
    await update.message.reply_text(f"⏳ Summarizing {len(last_100)} messages from General...")

    summary = await summarize_messages(last_100)
    await update.message.reply_text(f"📊 Chat Summary ({len(last_100)} messages)\n\n{summary}")


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command (DM): show most active members by message count."""
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in authenticated_admins:
        await update.message.reply_text("🔒 You need to authenticate first. Use /admin")
        return

    if not message_buffer:
        await update.message.reply_text("📭 No messages tracked yet.")
        return

    counter = Counter()
    name_map = {}
    for m in message_buffer:
        counter[m["user_id"]] += 1
        handle = f"@{m['username']}" if m["username"] else m["display_name"]
        name_map[m["user_id"]] = handle

    top_10 = counter.most_common(10)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for rank, (uid, count) in enumerate(top_10, 1):
        prefix = medals[rank - 1] if rank <= 3 else f"  {rank}."
        lines.append(f"{prefix} {name_map[uid]} — {count} msgs")

    total = sum(counter.values())
    text = f"📈 Activity Leaderboard\n{'─' * 25}\n\n" + "\n".join(lines) + f"\n\n📊 Total: {total} messages tracked"
    await update.message.reply_text(text)


async def handle_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command (DM): ask AI about the member base."""
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in authenticated_admins:
        await update.message.reply_text("🔒 You need to authenticate first. Use /admin")
        return

    members = get_all_members()
    if not members:
        await update.message.reply_text("📭 No members in the database yet.")
        return

    # Check if question was passed inline: /members how many developers?
    question = " ".join(context.args) if context.args else ""
    if question:
        await update.message.reply_text("⏳ Analyzing member data...")
        answer = await answer_members_question(question, members)
        await update.message.reply_text(f"👥 {answer}")
    else:
        pending_members_query.add(update.effective_user.id)
        await update.message.reply_text(
            f"👥 There are *{len(members)}* registered members.\n\n"
            "💬 What would you like to know about them?\nType your question below:",
            parse_mode="Markdown",
        )


async def handle_members_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the follow-up question after /members."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    if user_id not in pending_members_query:
        return

    pending_members_query.discard(user_id)

    members = get_all_members()
    await update.message.reply_text("⏳ Analyzing member data...")

    back_button = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back to Menu", callback_data="admin_back")]])
    try:
        answer = await answer_members_question(update.message.text, members)
        await update.message.reply_text(f"👥 {answer}", reply_markup=back_button)
    except Exception as e:
        logger.error(f"Members question failed: {e}")
        await update.message.reply_text("❌ Something went wrong. Try again.", reply_markup=back_button)


async def enforce_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete messages from unverified users and prompt them to verify."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    # Allow messages in the verify topic (unverified users interact here)
    if update.message.message_thread_id == config.VERIFY_TOPIC_ID:
        return

    # Never filter the bot's own messages
    me = await context.bot.get_me()
    if user.id == me.id:
        return

    # Allow group admins/creators
    try:
        chat_member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
        if chat_member.status in ("administrator", "creator"):
            return
    except Exception:
        pass

    # Check if user is verified
    if get_member(user.id) is not None:
        return

    # UNVERIFIED — delete message
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception as e:
        logger.error(f"Failed to delete message from unverified user {user.id}: {e}")

    # Send nag with cooldown to avoid spam
    now = time.time()
    last_nag = verification_nag_cooldown.get(user.id, 0)
    if now - last_nag > VERIFICATION_NAG_COOLDOWN:
        verification_nag_cooldown[user.id] = now
        chat_id = update.effective_chat.id
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "✨ Verify Now",
                url=f"https://t.me/{me.username}?start=verify_{chat_id}",
            )]]
        )
        nag_msg = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=update.message.message_thread_id,
            text=(
                f"🔒 Hey {user.full_name}! You need to verify before you can "
                "send messages here.\n\n"
                "Tap below to get started — it only takes a minute!"
            ),
            reply_markup=keyboard,
        )
        # Track nag so we can delete it after verification
        verification_nag_messages.setdefault(user.id, []).append(
            (chat_id, nag_msg.message_id, update.message.message_thread_id)
        )

    raise ApplicationHandlerStop


async def handle_link_safeguard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-warn when someone posts a link and forward to admin for review."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not update.message:
        return
    # Don't trigger on messages in admin topic
    if update.message.message_thread_id == config.ADMIN_TOPIC_ID:
        return

    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    thread_id = update.message.message_thread_id
    user = update.effective_user
    display = f"@{user.username}" if user.username else user.full_name

    # Auto-reply safety warning in the same topic
    await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        reply_to_message_id=msg_id,
        text=(
            "🛡️ *Link Detected — Stay Safe!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔍 Verify the link before clicking\n"
            "🔑 Never share your private keys\n"
            "🚫 Watch out for scams & phishing\n\n"
            "_Automated security alert_"
        ),
        parse_mode="Markdown",
    )

    # Forward to all authenticated admins via DM with delete button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Delete Message", callback_data=f"dellink_{chat_id}_{msg_id}")],
    ])
    for admin_id in authenticated_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🔗 Link Alert\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 Posted by: {display}\n"
                    f"💬 Message:\n{update.message.text}\n\n"
                    "Tap below to remove this message if suspicious."
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to forward link to admin {admin_id}: {e}")


async def handle_delete_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler: admin clicks delete button to remove a link message."""
    query = update.callback_query
    if not query.data.startswith("dellink_"):
        return

    await query.answer()

    parts = query.data.split("_")
    # dellink_{chat_id}_{message_id}
    try:
        target_chat_id = int(parts[1])
        target_msg_id = int(parts[2])
    except (IndexError, ValueError):
        await query.edit_message_text("Failed to parse delete request.")
        return

    try:
        await context.bot.delete_message(chat_id=target_chat_id, message_id=target_msg_id)
        admin_name = query.from_user.full_name
        await query.edit_message_text(
            f"{query.message.text}\n\n✅ Deleted by {admin_name}",
        )
    except Exception as e:
        await query.edit_message_text(
            f"{query.message.text}\n\n❌ Could not delete: {e}",
        )


async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin portal button clicks."""
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in authenticated_admins:
        await query.answer("Session expired. Use /admin to log in again.", show_alert=True)
        return

    await query.answer()
    action = query.data
    back_button = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back to Menu", callback_data="admin_back")]])

    if action == "admin_summary":
        general_msgs = [m for m in message_buffer if m["thread_id"] is None]
        if not general_msgs:
            await query.edit_message_text(
                "📭 No messages tracked from General yet.\n\nThe bot only tracks messages received since it started running.",
                reply_markup=back_button,
            )
            return

        last_100 = general_msgs[-100:]
        await query.edit_message_text(f"⏳ Summarizing {len(last_100)} messages from General...")

        summary = await summarize_messages(last_100)
        await query.edit_message_text(
            f"📊 Chat Summary ({len(last_100)} messages)\n{'━' * 25}\n\n{summary}",
            reply_markup=back_button,
        )

    elif action == "admin_stats":
        if not message_buffer:
            await query.edit_message_text(
                "📭 No messages tracked yet.",
                reply_markup=back_button,
            )
            return

        counter = Counter()
        name_map = {}
        for m in message_buffer:
            counter[m["user_id"]] += 1
            handle = f"@{m['username']}" if m["username"] else m["display_name"]
            name_map[m["user_id"]] = handle

        top_10 = counter.most_common(10)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, (uid, count) in enumerate(top_10, 1):
            prefix = medals[rank - 1] if rank <= 3 else f"  {rank}."
            lines.append(f"{prefix} {name_map[uid]} — {count} msgs")

        total = sum(counter.values())
        text = f"📈 Activity Leaderboard\n{'━' * 25}\n\n" + "\n".join(lines) + f"\n\n📊 Total: {total} messages tracked"
        await query.edit_message_text(
            text,
            reply_markup=back_button,
        )

    elif action == "admin_members":
        members = get_all_members()
        if not members:
            await query.edit_message_text(
                "📭 No members in the database yet.",
                reply_markup=back_button,
            )
            return

        pending_members_query.add(user_id)
        await query.edit_message_text(
            f"👥 There are *{len(members)}* registered members.\n\n"
            "💬 What would you like to know about them?\nType your question below:",
            parse_mode="Markdown",
        )

    elif action == "admin_logout":
        authenticated_admins.discard(user_id)
        await query.edit_message_text("👋 Logged out. Use /admin to log in again.")

    elif action == "admin_back":
        await query.edit_message_text(
            "🔐 Admin Portal\n━━━━━━━━━━━━━━━━━━━━\n\nWhat would you like to do?",
            reply_markup=admin_menu_keyboard(),
        )


CONTACT_KEYWORDS = [
    "who", "contact", "in charge", "person", "reach", "responsible",
    "talk to", "message", "dm", "point of contact", "poc", "pic",
    "lead", "head", "manager", "admin", "founder", "superteam", "stmy",
]


async def handle_contact_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-reply when someone asks who to contact in the group."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not update.message or not update.message.text:
        return

    text_lower = update.message.text.lower()

    # Quick keyword pre-filter to avoid calling LLM on every message
    if not any(kw in text_lower for kw in CONTACT_KEYWORDS):
        return

    # Confirm with LLM
    try:
        is_query = await is_contact_query(update.message.text)
    except Exception as e:
        logger.error(f"Contact query LLM check failed: {e}")
        return

    if not is_query:
        return

    await update.message.reply_text(
        f"💡 For all Superteam MY related questions, feel free to reach out to:\n\n"
        f"👉 {config.PIC_HANDLES}\n\n"
        "They'll be happy to help!"
    )


async def handle_verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /verify in the verify topic — let existing users check their registration."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if update.message.message_thread_id != config.VERIFY_TOPIC_ID:
        return

    user_id = update.effective_user.id
    pending_handle_check.add(user_id)
    await update.message.reply_text("🔍 Please type your Telegram handle (e.g. @yourname):")


async def handle_handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the handle input after /verify in the verify topic."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if update.message.message_thread_id != config.VERIFY_TOPIC_ID:
        return
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    if user_id not in pending_handle_check:
        return

    pending_handle_check.discard(user_id)
    handle = update.message.text.strip()

    member = get_member_by_handle(handle)
    if member:
        about = member.get("about", member.get("profession", "N/A"))
        await update.message.reply_text(
            f"✅ You're already registered!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Name: {member['name']}\n"
            f"💼 About: {about}\n"
            f"📅 Verified: {member['verified_at'][:10]}"
        )
    else:
        # Not in DB — prompt them to register via DM
        bot_username = (await context.bot.get_me()).username
        chat_id = update.effective_chat.id
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "✨ Start Registration",
                url=f"https://t.me/{bot_username}?start=verify_{chat_id}",
            )]]
        )
        await update.message.reply_text(
            f"❌ {handle} is not registered yet.\n\n"
            "Tap the button below to complete verification!",
            reply_markup=keyboard,
        )


async def handle_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to discover topic thread IDs. Run /setup in each topic."""
    if update.effective_chat.type == "private":
        await update.message.reply_text("Run this command inside your group topics.")
        return

    thread_id = update.message.message_thread_id
    topic_name = "General (no topic)" if thread_id is None else f"Topic thread ID: {thread_id}"

    await update.message.reply_text(
        f"Chat ID: `{update.effective_chat.id}`\n{topic_name}",
        parse_mode="Markdown",
    )


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")
    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set in .env")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Handler: detect new members (chat_member update — works when bot is admin)
    app.add_handler(
        ChatMemberHandler(handle_new_member, ChatMemberHandler.CHAT_MEMBER)
    )

    # Handler: detect new members (service message fallback — works without admin)
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            handle_new_member_service,
        )
    )

    # Handler: /start in DM (verification deep link)
    app.add_handler(CommandHandler("start", handle_start, filters=filters.ChatType.PRIVATE))

    # Handler: /verify in verify topic (existing users check registration)
    app.add_handler(CommandHandler("verify", handle_verify_command))

    # Handler: /setup in group (admin tool)
    app.add_handler(CommandHandler("setup", handle_setup))

    # Handler: /admin in DM (admin authentication)
    app.add_handler(CommandHandler("admin", handle_admin, filters=filters.ChatType.PRIVATE))

    # Handler: /logout in DM
    app.add_handler(CommandHandler("logout", handle_logout, filters=filters.ChatType.PRIVATE))

    # Handler: /summary in DM (admin only)
    app.add_handler(CommandHandler("summary", handle_summary, filters=filters.ChatType.PRIVATE))

    # Handler: /stats in DM (admin only)
    app.add_handler(CommandHandler("stats", handle_stats, filters=filters.ChatType.PRIVATE))

    # Handler: /members in DM (admin only)
    app.add_handler(CommandHandler("members", handle_members, filters=filters.ChatType.PRIVATE))

    # Handler: admin menu button clicks
    app.add_handler(CallbackQueryHandler(handle_admin_menu, pattern=r"^admin_"))

    # Handler: admin delete link callback
    app.add_handler(CallbackQueryHandler(handle_delete_link, pattern=r"^dellink_"))

    # Handler: enforce verification — delete messages from unverified users
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL & ~filters.COMMAND,
            enforce_verification,
        ),
        group=1,
    )

    # Handler: link safeguard (group messages with URLs)
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & (filters.Entity("url") | filters.Entity("text_link")),
            handle_link_safeguard,
        ),
        group=2,
    )

    # Handler: track all group messages for /summary and /stats
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            track_message,
        ),
        group=3,
    )

    # Handler: auto-reply to contact/PIC questions in group
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_contact_query,
        ),
        group=4,
    )

    # Handler: handle input after /verify in verify topic
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_handle_input,
        ),
        group=5,
    )

    # Handler: DM messages — admin password, members question, redo, verification Q&A
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_admin_password,
        ),
        group=0,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_members_question,
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_redo_answer,
        ),
        group=2,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_dm_message,
        ),
        group=3,
    )

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
