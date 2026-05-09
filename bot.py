import os
import sqlite3
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

drafts = {}  # Черновики сообщений
admin_state = {} # Состояния для админ-панели (рассылка)

# =========================
# HELPERS
# =========================
def now(): return datetime.now().strftime("%d.%m %H:%M")

def is_banned(user_id):
    cur.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    res = cur.fetchone()
    return res and res[0] == 1

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cur.execute("INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)", (uid, now()))
    conn.commit()
    
    kb = [
        [KeyboardButton("✉️ Отправить сообщение")],
        [KeyboardButton("❓ Справка")]
    ]
    # Добавляем кнопку админки только владельцу
    if uid == OWNER_ID:
        kb.append([KeyboardButton("⚙️ Админ-панель")])
        
    await update.message.reply_text(
        "👋 **Добро пожаловать!**\n\nЯ помогу тебе отправить анонимное сообщение администратору. Твоя личность останется в секрете.",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Справка по использованию*\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "1️⃣ **Отправка:** Просто напиши текст или прикрепи фото/видео. Бот создаст черновик.\n"
        "2️⃣ **Подтверждение:** Нажми «✅ Отправить», чтобы сообщение ушло админу.\n"
        "3️⃣ **Анонимность:** Админ не видит твой профиль, только ID сообщения.\n"
        "4️⃣ **Ответы:** Ты получишь уведомление, когда админ ответит тебе.\n\n"
        "💡 *Если хочешь что-то исправить, используй кнопку «✏️ Изменить» на этапе предпросмотра.*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    cur.execute("SELECT COUNT(*) FROM users")
    u_count = cur.fetchone()[0]
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton("📢 Сделать рассылку", callback_data="adm_broadcast")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="adm_close")]
    ])
    
    await update.message.reply_text(
        f"⚙️ **Панель управления**\n\nВсего пользователей: `{u_count}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )

async def filter_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if is_banned(user.id): return
    
    # Обработка кнопок меню
    if msg.text == "❓ Справка":
        await help_command(update, context)
        return
    if msg.text == "⚙️ Админ-панель" and user.id == OWNER_ID:
        await admin_panel(update, context)
        return
    if msg.text == "✉️ Отправить сообщение":
        await update.message.reply_text("✍️ Пришли текст, фото или видео:")
        return

    # Если админ в процессе рассылки
    if user.id == OWNER_ID and admin_state.get(user.id) == "waiting_broadcast":
        admin_state[user.id] = f"confirm_{msg.text}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, отправить", callback_data="bc_yes"),
             InlineKeyboardButton("❌ Отмена", callback_data="bc_no")]
        ])
        await msg.reply_text(f"📢 **Текст рассылки:**\n\n{msg.text}\n\nОтправить всем?", reply_markup=kb, parse_mode="Markdown")
        return

    # Обычный пользователь (или админ пишет не в режиме рассылки)
    if user.id == OWNER_ID: return

    m_type, file_id = "text", None
    if msg.photo: m_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video: m_type, file_id = "video", msg.video.file_id
    elif msg.voice: m_type, file_id = "voice", msg.voice.file_id

    drafts[user.id] = {"type": m_type, "content": msg.text, "file_id": file_id, "caption": msg.caption or ""}
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="confirm_send"),
         InlineKeyboardButton("✏️ Изменить", callback_data="edit_msg")]
    ])
    await update.message.reply_text("👀 **Предпросмотр сообщения:**\nБот отправит это анонимно. Подтверждаешь?", reply_markup=kb, parse_mode="Markdown")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    
    # Логика пользователя
    if q.data == "confirm_send":
        if uid not in drafts: return
        d = drafts.pop(uid)
        mid = save_message(uid, d["type"], d["content"], d["file_id"], d["caption"])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Ответить", callback_data=f"rep_{mid}"), InlineKeyboardButton("🚫 Бан", callback_data=f"ban_{uid}")]])
        h = f"📩 #{mid}"
        if d["type"] == "text": await context.bot.send_message(OWNER_ID, f"{h}\n\n{d['content']}", reply_markup=kb)
        elif d["type"] == "photo": await context.bot.send_photo(OWNER_ID, d["file_id"], caption=f"{h}\n{d['caption']}", reply_markup=kb)
        elif d["type"] == "video": await context.bot.send_video(OWNER_ID, d["file_id"], caption=f"{h}\n{d['caption']}", reply_markup=kb)
        await q.message.edit_text("🚀 **Отправлено!**", parse_mode="Markdown")

    elif q.data == "edit_msg":
        drafts.pop(uid, None)
        await q.message.edit_text("✏️ Черновик удален. Жду новое сообщение:")

    # Логика админа
    elif q.data == "adm_stats":
        cur.execute("SELECT COUNT(*) FROM messages")
        m_count = cur.fetchone()[0]
        await q.message.reply_text(f"📊 Всего получено сообщений: `{m_count}`", parse_mode="Markdown")
    
    elif q.data == "adm_broadcast":
        admin_state[uid] = "waiting_broadcast"
        await q.message.reply_text("📢 Введи текст сообщения для рассылки всем пользователям:")

    elif q.data == "bc_yes":
        state = admin_state.get(uid, "")
        if state.startswith("confirm_"):
            text = state.replace("confirm_", "")
            cur.execute("SELECT user_id FROM users WHERE is_banned=0")
            users = cur.fetchall()
            count = 0
            for (u,) in users:
                try: 
                    await context.bot.send_message(u, f"📢 **Объявление:**\n\n{text}", parse_mode="Markdown")
                    count += 1
                except: continue
            await q.message.edit_text(f"✅ Рассылка завершена! Получили: `{count}` чел.", parse_mode="Markdown")
            admin_state.pop(uid, None)

    elif q.data.startswith("rep_"):
        context.user_data["reply_to"] = q.data.split("_")[1]
        await q.message.reply_text(f"✍️ Пиши ответ для сообщения #{context.user_data['reply_to']}:")

    elif q.data.startswith("ban_"):
        target = int(q.data.split("_")[1])
        cur.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (target,))
        conn.commit()
        await q.message.reply_text(f"🚫 Пользователь `{target}` заблокирован.", parse_mode="Markdown")

    elif q.data == "adm_close" or q.data == "bc_no":
        admin_state.pop(uid, None)
        await q.message.delete()

async def admin_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID or "reply_to" not in context.user_data: return
    mid = context.user_data.pop("reply_to")
    cur.execute("SELECT user_id FROM messages WHERE id=?", (mid,))
    row = cur.fetchone()
    if row:
        try:
            await context.bot.send_message(row[0], f"ответ йокосо:\n\n{update.message.text}")
            await update.message.reply_text("✅ Ответ доставлен!")
        except: await update.message.reply_text("❌ Ошибка доставки.")

def save_message(uid, t, c, f, cap):
    cur.execute("INSERT INTO messages (user_id, type, content, file_id, caption, time) VALUES (?, ?, ?, ?, ?, ?)", (uid, t, c, f, cap, now()))
    conn.commit()
    return cur.lastrowid

# =========================
# SERVER & MAIN
# =========================
class H(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def do_HEAD(self): self.send_response(200); self.end_headers()

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), H).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Chat(OWNER_ID) & ~filters.COMMAND, admin_reply_handler), group=1)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, filter_incoming), group=2)
    print("Бот Йокосо 2.0 запущен...")
    app.run_polling()
