import os
import sqlite3
import time
import asyncio
from datetime import datetime

from flask import Flask, request

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# =========================
# ENV
# =========================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# =========================
# FLASK
# =========================
flask_app = Flask(__name__)

# =========================
# TELEGRAM APP
# =========================
app = Application.builder().token(TOKEN).build()

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_seen TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    content TEXT,
    file_id TEXT,
    caption TEXT,
    time TEXT
)
""")

conn.commit()

# =========================
# UI
# =========================
menu = ReplyKeyboardMarkup(
    [[KeyboardButton("✉️ Отправить сообщение")]],
    resize_keyboard=True
)

drafts = {}
waiting_edit = set()
reply_wait = {}

# =========================
# ANTISPAM
# =========================
last_message_time = {}
SPAM_DELAY = 3

def is_spam(user_id):
    now = time.time()

    if user_id in last_message_time:
        if now - last_message_time[user_id] < SPAM_DELAY:
            return True

    last_message_time[user_id] = now
    return False

# =========================
# HELPERS
# =========================
def now():
    return datetime.now().strftime("%H:%M")

def save_user(user_id):
    cur.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?)",
        (user_id, now())
    )
    conn.commit()

def save_message(user_id, type_, content=None, file_id=None, caption=None):
    cur.execute("""
    INSERT INTO messages
    (user_id, type, content, file_id, caption, time)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        type_,
        content,
        file_id,
        caption,
        now()
    ))

    conn.commit()
    return cur.lastrowid

# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    save_user(user.id)

    await update.message.reply_text(
        "👋 Анонимный бот работает",
        reply_markup=menu
    )

# =========================
# TEXT
# =========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    save_user(user.id)

    # антиспам
    if is_spam(user.id):
        await update.message.reply_text(
            "⏳ Не так быстро"
        )
        return

    # ответ владельца
    if user.id == OWNER_ID and user.id in reply_wait:
        msg_id = reply_wait[user.id]

        cur.execute(
            "SELECT user_id FROM messages WHERE id=?",
            (msg_id,)
        )

        row = cur.fetchone()

        if row:
            target_user = row[0]

            await context.bot.send_message(
                target_user,
                f"📩 Ответ:\n\n{text}"
            )

            await update.message.reply_text("✅ Ответ отправлен")

        del reply_wait[user.id]
        return

    # кнопка
    if text == "✉️ Отправить сообщение":
        waiting_edit.add(user.id)

        await update.message.reply_text(
            "✍️ Напиши сообщение"
        )
        return

    # предпросмотр
    if user.id in waiting_edit:

        drafts[user.id] = {
            "type": "text",
            "content": text
        }

        waiting_edit.remove(user.id)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Отправить",
                    callback_data="send"
                ),
                InlineKeyboardButton(
                    "✏️ Изменить",
                    callback_data="edit"
                )
            ]
        ])

        await update.message.reply_text(
            f"👀 Предпросмотр:\n\n{text}",
            reply_markup=kb
        )

# =========================
# PHOTO
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    save_user(user.id)

    photo = update.message.photo[-1].file_id
    caption = update.message.caption or ""

    drafts[user.id] = {
        "type": "photo",
        "file": photo,
        "caption": caption
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Отправить",
                callback_data="send"
            ),
            InlineKeyboardButton(
                "✏️ Изменить",
                callback_data="edit"
            )
        ]
    ])

    await update.message.reply_photo(
        photo,
        caption="👀 Предпросмотр",
        reply_markup=kb
    )

# =========================
# VIDEO
# =========================
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    save_user(user.id)

    video = update.message.video.file_id
    caption = update.message.caption or ""

    drafts[user.id] = {
        "type": "video",
        "file": video,
        "caption": caption
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Отправить",
                callback_data="send"
            ),
            InlineKeyboardButton(
                "✏️ Изменить",
                callback_data="edit"
            )
        ]
    ])

    await update.message.reply_video(
        video,
        caption="👀 Предпросмотр",
        reply_markup=kb
    )

# =========================
# STICKER
# =========================
async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    save_user(user.id)

    sticker = update.message.sticker.file_id

    drafts[user.id] = {
        "type": "sticker",
        "file": sticker
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Отправить",
                callback_data="send"
            ),
            InlineKeyboardButton(
                "✏️ Изменить",
                callback_data="edit"
            )
        ]
    ])

    await update.message.reply_sticker(
        sticker,
        reply_markup=kb
    )

# =========================
# CALLBACKS
# =========================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query

    await q.answer()

    user = q.from_user

    # отправка
    if q.data == "send":

        if user.id not in drafts:
            return

        d = drafts[user.id]

        msg_id = save_message(
            user.id,
            d["type"],
            d.get("content"),
            d.get("file"),
            d.get("caption")
        )

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩️ Ответить",
                    callback_data=f"reply_{msg_id}"
                )
            ]
        ])

        if d["type"] == "text":
            await context.bot.send_message(
                OWNER_ID,
                f"📩 #{msg_id}\n\n{d['content']}",
                reply_markup=kb
            )

        elif d["type"] == "photo":
            await context.bot.send_photo(
                OWNER_ID,
                d["file"],
                caption=f"📷 #{msg_id}",
                reply_markup=kb
            )

        elif d["type"] == "video":
            await context.bot.send_video(
                OWNER_ID,
                d["file"],
                caption=f"🎥 #{msg_id}",
                reply_markup=kb
            )

        elif d["type"] == "sticker":
            await context.bot.send_sticker(
                OWNER_ID,
                d["file"]
            )

            await context.bot.send_message(
                OWNER_ID,
                f"😀 #{msg_id}",
                reply_markup=kb
            )

        del drafts[user.id]

        await q.message.edit_text("✅ Отправлено")

    # изменить
    elif q.data == "edit":

        waiting_edit.add(user.id)

        await q.message.edit_text(
            "✏️ Отправь новое сообщение"
        )

    # ответить
    elif q.data.startswith("reply_"):

        if user.id != OWNER_ID:
            return

        msg_id = int(q.data.split("_")[1])

        reply_wait[user.id] = msg_id

        await q.message.reply_text(
            "✍️ Напиши ответ"
        )

# =========================
# HANDLERS
# =========================
app.add_handler(CommandHandler("start", start))

app.add_handler(CallbackQueryHandler(button))

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text
    )
)

app.add_handler(
    MessageHandler(
        filters.PHOTO,
        handle_photo
    )
)

app.add_handler(
    MessageHandler(
        filters.VIDEO,
        handle_video
    )
)

app.add_handler(
    MessageHandler(
        filters.Sticker.ALL,
        handle_sticker
    )
)

# =========================
# WEBHOOK
# =========================
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(
        request.get_json(force=True),
        app.bot
    )

    app.update_queue.put_nowait(update)

    return "ok"

@flask_app.route("/")
def home():
    return "Bot is running"

# =========================
# START
# =========================
async def run():
    await app.initialize()

    await app.bot.set_webhook(
        url=WEBHOOK_URL + "/webhook"
    )

    await app.start()

    flask_app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )

if __name__ == "__main__":
    asyncio.run(run())
