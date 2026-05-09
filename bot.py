import os
import asyncio
import sqlite3
import time
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# ======================
# 🔐 ENV
# ======================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

# ======================
# 📦 DB
# ======================
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

# ======================
# 🛡 ANTI SPAM
# ======================
last_message_time = {}

SPAM_DELAY = 3  # секунды

def is_spam(user_id):
    now = time.time()
    last = last_message_time.get(user_id, 0)

    if now - last < SPAM_DELAY:
        return True

    last_message_time[user_id] = now
    return False

# ======================
# UI
# ======================
menu = ReplyKeyboardMarkup(
    [[KeyboardButton("✉️ Отправить сообщение")]],
    resize_keyboard=True
)

drafts = {}
waiting_edit = set()

def now():
    return datetime.now().strftime("%H:%M")

# ======================
# 👤 SAVE USER
# ======================
def save_user(user_id):
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users VALUES (?, ?)",
            (user_id, now())
        )
        conn.commit()

# ======================
# 💾 SAVE MESSAGE
# ======================
def save_message(user_id, type_, content=None, file_id=None, caption=None):
    cur.execute("""
        INSERT INTO messages (user_id, type, content, file_id, caption, time)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, type_, content, file_id, caption, now()))
    conn.commit()
    return cur.lastrowid

# ======================
# START
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id)

    await update.message.reply_text(
        "👋 Анонимный бот работает\n\n✉️ Напиши сообщение",
        reply_markup=menu
    )

# ======================
# TEXT
# ======================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    save_user(user.id)

    # 🛡 ANTI SPAM
    if is_spam(user.id):
        await update.message.reply_text("⏳ Слишком быстро. Подожди немного.")
        return

    if text == "✉️ Отправить сообщение":
        waiting_edit.add(user.id)
        await update.message.reply_text("✍️ Напиши сообщение:")
        return

    if user.id in waiting_edit:

        drafts[user.id] = {"type": "text", "content": text}
        waiting_edit.remove(user.id)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отправить", callback_data="send"),
             InlineKeyboardButton("✏️ Изменить", callback_data="edit")]
        ])

        await update.message.reply_text(f"👀 Предпросмотр:\n\n{text}", reply_markup=kb)
        return

# ======================
# PHOTO
# ======================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id)

    photo = update.message.photo[-1].file_id
    caption = update.message.caption or ""

    drafts[user.id] = {"type": "photo", "file": photo, "caption": caption}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="send"),
         InlineKeyboardButton("✏️ Изменить", callback_data="edit")]
    ])

    await update.message.reply_photo(photo, caption="👀 Предпросмотр", reply_markup=kb)

# ======================
# VIDEO
# ======================
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id)

    video = update.message.video.file_id
    caption = update.message.caption or ""

    drafts[user.id] = {"type": "video", "file": video, "caption": caption}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="send"),
         InlineKeyboardButton("✏️ Изменить", callback_data="edit")]
    ])

    await update.message.reply_video(video, caption="👀 Предпросмотр", reply_markup=kb)

# ======================
# STICKER
# ======================
async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id)

    sticker = update.message.sticker.file_id

    drafts[user.id] = {"type": "sticker", "file": sticker}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="send"),
         InlineKeyboardButton("✏️ Изменить", callback_data="edit")]
    ])

    await update.message.reply_sticker(sticker, reply_markup=kb)

# ======================
# CALLBACK
# ======================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = q.from_user

    if user.id not in drafts:
        return

    d = drafts[user.id]

    # SEND
    if q.data == "send":

        msg_id = save_message(user.id, d["type"], d.get("content"), d.get("file"), d.get("caption"))

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Ответить", callback_data=f"reply_{msg_id}")]
        ])

        if d["type"] == "text":
            await context.bot.send_message(OWNER_ID, f"📩 #{msg_id}\n\n{d['content']}", reply_markup=kb)

        elif d["type"] == "photo":
            await context.bot.send_photo(OWNER_ID, d["file"], caption=f"📷 #{msg_id}", reply_markup=kb)

        elif d["type"] == "video":
            await context.bot.send_video(OWNER_ID, d["file"], caption=f"🎥 #{msg_id}", reply_markup=kb)

        elif d["type"] == "sticker":
            await context.bot.send_sticker(OWNER_ID, d["file"])
            await context.bot.send_message(OWNER_ID, f"😀 #{msg_id}", reply_markup=kb)

        del drafts[user.id]
        await q.message.edit_text("✅ Отправлено")
        return

    # EDIT
    if q.data == "edit":
        await q.message.edit_text("✏️ Напиши заново сообщение")
        return

# ======================
# RUN
# ======================
from telegram.ext import Application

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button))

app.add_handler(MessageHandler(filters.TEXT, handle_text))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.VIDEO, handle_video))
app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))

print("Bot started")
app.run_polling()
