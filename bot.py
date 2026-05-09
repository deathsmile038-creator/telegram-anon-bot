import os
import sqlite3
import time
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

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
# CONFIG & ENV
# =========================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

# Для сохранения данных на бесплатных хостингах (если есть подключенный диск)
# Если диска нет, оставь просто "bot.db", но помни про очистку при перезагрузке
DB_PATH = os.environ.get("DB_PATH", "bot.db")

# =========================
# DATABASE
# =========================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_seen TEXT)")
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
# LOGIC & HANDLERS
# =========================
menu = ReplyKeyboardMarkup([[KeyboardButton("✉️ Отправить сообщение")]], resize_keyboard=True)
drafts = {}
waiting_edit = set()
reply_wait = {}
last_message_time = {}
SPAM_DELAY = 3

def is_spam(user_id):
    now = time.time()
    if user_id in last_message_time and now - last_message_time[user_id] < SPAM_DELAY:
        return True
    last_message_time[user_id] = now
    return False

def now(): return datetime.now().strftime("%H:%M")

def save_user(user_id):
    cur.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user_id, now()))
    conn.commit()

def save_message(user_id, type_, content=None, file_id=None, caption=None):
    cur.execute("INSERT INTO messages (user_id, type, content, file_id, caption, time) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, type_, content, file_id, caption, now()))
    conn.commit()
    return cur.lastrowid

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user.id)
    await update.message.reply_text("👋 Анонимный бот работает", reply_markup=menu)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    save_user(user.id)

    if is_spam(user.id):
        await update.message.reply_text("⏳ Не так быстро")
        return

    if user.id == OWNER_ID and user.id in reply_wait:
        msg_id = reply_wait[user.id]
        cur.execute("SELECT user_id FROM messages WHERE id=?", (msg_id,))
        row = cur.fetchone()
        if row:
            await context.bot.send_message(row[0], f"📩 Ответ:\n\n{text}")
            await update.message.reply_text("✅ Ответ отправлен")
        del reply_wait[user.id]
        return

    if text == "✉️ Отправить сообщение":
        waiting_edit.add(user.id)
        await update.message.reply_text("✍️ Напиши сообщение")
        return

    if user.id in waiting_edit:
        drafts[user.id] = {"type": "text", "content": text}
        waiting_edit.remove(user.id)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Отправить", callback_data="send"), 
                                    InlineKeyboardButton("✏️ Изменить", callback_data="edit")]])
        await update.message.reply_text(f"👀 Предпросмотр:\n\n{text}", reply_markup=kb)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user

    if q.data == "send" and user.id in drafts:
        d = drafts[user.id]
        msg_id = save_message(user.id, d["type"], d.get("content"), d.get("file"), d.get("caption"))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Ответить", callback_data=f"reply_{msg_id}")]])
        await context.bot.send_message(OWNER_ID, f"📩 #{msg_id}\n\n{d.get('content', 'Медиа')}", reply_markup=kb)
        del drafts[user.id]
        await q.message.edit_text("✅ Отправлено")
    elif q.data == "edit":
        waiting_edit.add(user.id)
        await q.message.edit_text("✏️ Отправь новое сообщение")
    elif q.data.startswith("reply_") and user.id == OWNER_ID:
        reply_wait[user.id] = int(q.data.split("_")[1])
        await q.message.reply_text("✍️ Напиши ответ")

# =========================
# KEEP ALIVE SERVER
# =========================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    httpd = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    httpd.serve_forever()

# =========================
# MAIN
# =========================
def main():
    # Запускаем фоновый сервер, чтобы хостинг не отключал бота
    threading.Thread(target=run_health_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
