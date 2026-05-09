import os
import sqlite3
import time
import asyncio
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# =========================
# CONFIG & ENV
# =========================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "bot.db")

# =========================
# DATABASE
# =========================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_seen TEXT, is_banned INTEGER DEFAULT 0)")
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
# HELPERS
# =========================
def now(): return datetime.now().strftime("%d.%m %H:%M")

def is_banned(user_id):
    cur.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    res = cur.fetchone()
    return res and res[0] == 1

def save_user(user_id):
    cur.execute("INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)", (user_id, now()))
    conn.commit()

def save_message(user_id, type_, content=None, file_id=None, caption=None):
    cur.execute("INSERT INTO messages (user_id, type, content, file_id, caption, time) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, type_, content, file_id, caption, now()))
    conn.commit()
    return cur.lastrowid

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user.id)
    menu = ReplyKeyboardMarkup([[KeyboardButton("✉️ Отправить сообщение")]], resize_keyboard=True)
    await update.message.reply_text("👋 Бот готов принимать анонимные сообщения (текст, фото, видео)!", reply_markup=menu)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    cur.execute("SELECT COUNT(*) FROM users")
    u_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM messages")
    m_count = cur.fetchone()[0]
    await update.message.reply_text(f"📊 Статистика:\nЮзеров: {u_count}\nСообщений: {m_count}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("❌ Введи текст: /broadcast Привет всем")
        return
    
    cur.execute("SELECT user_id FROM users WHERE is_banned=0")
    users = cur.fetchall()
    count = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(uid, f"📢 Сообщение от администрации:\n\n{text}")
            count += 1
        except: continue
    await update.message.reply_text(f"✅ Рассылка завершена. Получили: {count} чел.")

async def filter_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id) or user.id == OWNER_ID: return
    
    save_user(user.id)
    msg = update.message
    file_id = None
    m_type = "text"
    content = msg.text
    caption_text = msg.caption or ""

    # Определение типа медиа
    if msg.photo:
        m_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video:
        m_type, file_id = "video", msg.video.file_id
    elif msg.voice:
        m_type, file_id = "voice", msg.voice.file_id
    elif msg.audio:
        m_type, file_id = "audio", msg.audio.file_id
    elif msg.document:
        m_type, file_id = "document", msg.document.file_id
    elif msg.sticker:
        m_type, file_id = "sticker", msg.sticker.file_id

    msg_db_id = save_message(user.id, m_type, content, file_id, caption_text)
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Ответить", callback_data=f"rep_{msg_db_id}"),
         InlineKeyboardButton("🚫 Бан", callback_data=f"ban_{user.id}")]
    ])

    # Пересылка владельцу
    header = f"📩 #{msg_db_id}"
    try:
        if m_type == "text":
            if content == "✉️ Отправить сообщение": return # игнорируем нажатие кнопки
            await context.bot.send_message(OWNER_ID, f"{header}\n\n{content}", reply_markup=kb)
        elif m_type == "photo":
            await context.bot.send_photo(OWNER_ID, file_id, caption=f"{header}\n{caption_text}", reply_markup=kb)
        elif m_type == "video":
            await context.bot.send_video(OWNER_ID, file_id, caption=f"{header}\n{caption_text}", reply_markup=kb)
        elif m_type == "voice":
            await context.bot.send_voice(OWNER_ID, file_id, caption=header, reply_markup=kb)
        elif m_type == "sticker":
            await context.bot.send_sticker(OWNER_ID, file_id)
            await context.bot.send_message(OWNER_ID, header, reply_markup=kb)
        elif m_type == "document":
            await context.bot.send_document(OWNER_ID, file_id, caption=f"{header}\n{caption_text}", reply_markup=kb)
        
        await update.message.reply_text("✅ Сообщение отправлено!")
    except Exception as e:
        print(f"Ошибка пересылки: {e}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if q.data.startswith("ban_"):
        uid = int(q.data.split("_")[1])
        cur.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
        conn.commit()
        await q.message.reply_text(f"❌ Пользователь {uid} заблокирован.")
        
    elif q.data.startswith("rep_"):
        msg_id = q.data.split("_")[1]
        context.user_data["reply_to"] = msg_id
        await q.message.reply_text(f"✍️ Напиши ответ для #{msg_id}:")

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID or "reply_to" not in context.user_data:
        return
    
    msg_id = context.user_data.pop("reply_to")
    cur.execute("SELECT user_id FROM messages WHERE id=?", (msg_id,))
    row = cur.fetchone()
    
    if row:
        try:
            # Админ может отвечать текстом или пересылать медиа в ответ
            if update.message.text:
                await context.bot.send_message(row[0], f"📩 Ответ от администрации:\n\n{update.message.text}")
            elif update.message.photo:
                await context.bot.send_photo(row[0], update.message.photo[-1].file_id, caption="📩 Ответ от администрации")
            
            await update.message.reply_text("✅ Ответ доставлен!")
        except:
            await update.message.reply_text("❌ Не удалось отправить ответ.")

# =========================
# WEB SERVER (Health Check)
# =========================
class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def do_HEAD(self): self.send_response(200); self.end_headers()

def run_srv():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), HealthCheck).serve_forever()

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    threading.Thread(target=run_srv, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Сначала проверяем, не является ли сообщение ответом админа
    app.add_handler(MessageHandler(filters.Chat(OWNER_ID) & ~filters.COMMAND, admin_reply), group=1)
    # Затем обрабатываем всё остальное от пользователей
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, filter_incoming), group=2)
    
    print("Бот запущен с поддержкой медиа...")
    app.run_polling()
