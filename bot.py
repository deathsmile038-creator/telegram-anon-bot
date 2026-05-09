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
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, 
    content TEXT, file_id TEXT, caption TEXT, time TEXT
)
""")
conn.commit()

drafts = {} # Черновики

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
    menu = ReplyKeyboardMarkup([
        [KeyboardButton("✉️ Отправить сообщение")],
        [KeyboardButton("❓ Помощь")]
    ], resize_keyboard=True)
    await update.message.reply_text("👋 Привет! Пришли сообщение, и я передам его анонимно.", reply_markup=menu)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **Справка:**\n\n"
        "1. Просто пришлите текст или медиа.\n"
        "2. Нажмите '✅ Отправить' для подтверждения.\n"
        "3. Кнопка '✏️ Изменить' позволит переписать сообщение.\n\n"
        "Админ ответит вам анонимно."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def filter_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id) or user.id == OWNER_ID: return
    
    msg = update.message
    if msg.text == "❓ Помощь":
        await help_command(update, context)
        return
    if msg.text == "✉️ Отправить сообщение":
        await update.message.reply_text("✍️ Жду ваше сообщение:")
        return

    # Определяем тип для черновика
    m_type, file_id = "text", None
    if msg.photo: m_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video: m_type, file_id = "video", msg.video.file_id
    elif msg.voice: m_type, file_id = "voice", msg.voice.file_id

    drafts[user.id] = {
        "type": m_type,
        "content": msg.text,
        "file_id": file_id,
        "caption": msg.caption or ""
    }

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="confirm_send"),
         InlineKeyboardButton("✏️ Изменить", callback_data="edit_msg")]
    ])
    await update.message.reply_text("👀 Проверь сообщение перед отправкой:", reply_markup=kb)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    
    if q.data == "confirm_send":
        if uid not in drafts: return
        d = drafts.pop(uid)
        mid = save_message(uid, d["type"], d["content"], d["file_id"], d["caption"])
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Ответить", callback_data=f"rep_{mid}"),
             InlineKeyboardButton("🚫 Бан", callback_data=f"ban_{uid}")]
        ])

        h = f"📩 #{mid}"
        if d["type"] == "text":
            await context.bot.send_message(OWNER_ID, f"{h}\n\n{d['content']}", reply_markup=kb)
        elif d["type"] == "photo":
            await context.bot.send_photo(OWNER_ID, d["file_id"], caption=f"{h}\n{d['caption']}", reply_markup=kb)
        elif d["type"] == "video":
            await context.bot.send_video(OWNER_ID, d["file_id"], caption=f"{h}\n{d['caption']}", reply_markup=kb)
        
        await q.message.edit_text("🚀 Отправлено!")

    elif q.data == "edit_msg":
        drafts.pop(uid, None)
        await q.message.edit_text("✏️ Черновик удален. Пришли новое сообщение:")

    elif q.data.startswith("ban_"):
        target_id = int(q.data.split("_")[1])
        cur.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (target_id,))
        conn.commit()
        await q.message.reply_text(f"🚫 Пользователь {target_id} заблокирован.")
        
    elif q.data.startswith("rep_"):
        context.user_data["reply_to"] = q.data.split("_")[1]
        await q.message.reply_text(f"✍️ Пишем ответ для #{context.user_data['reply_to']}:")

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID or "reply_to" not in context.user_data: return
    
    msg_id = context.user_data.pop("reply_to")
    cur.execute("SELECT user_id FROM messages WHERE id=?", (msg_id,))
    row = cur.fetchone()
    
    if row:
        try:
            # Использование "ответ йокосо" в начале сообщения
            reply_text = f"ответ йокосо:\n\n{update.message.text}"
            await context.bot.send_message(row[0], reply_text)
            await update.message.reply_text("✅ Доставлено")
        except:
            await update.message.reply_text("❌ Ошибка (возможно, бот заблокирован)")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    cur.execute("SELECT COUNT(*) FROM users")
    u = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM messages")
    m = cur.fetchone()[0]
    await update.message.reply_text(f"📊 Юзеров: {u}\nСообщений: {m}")

# =========================
# SERVER & RUN
# =========================
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def do_HEAD(self): self.send_response(200); self.end_headers()

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Chat(OWNER_ID) & ~filters.COMMAND, admin_reply), group=1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, filter_incoming), group=2)
    
    print("Бот обновлен...")
    app.run_polling()
