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

drafts = {}  
admin_state = {} 

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
    
    # Кнопки для обычного пользователя
    user_kb = [[KeyboardButton("✉️ Отправить сообщение")], [KeyboardButton("❓ Справка")]]
    
    # Если зашел админ, добавляем ему кнопку управления
    if uid == OWNER_ID:
        user_kb.append([KeyboardButton("⚙️ Админ-панель")])
        
    await update.message.reply_text(
        "👋 **Добро пожаловать в анонимный чат!**\n\nПришли сообщение, и я передам его владельцу анонимно.",
        reply_markup=ReplyKeyboardMarkup(user_kb, resize_keyboard=True),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **Справка по боту**\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        "• Пришли любой текст или файл.\n"
        "• Проверь предпросмотр.\n"
        "• Нажми «✅ Отправить».\n\n"
        "Владелец ответит тебе как **йокосо**."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_panel_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Функция вызова админки через кнопку или команду"""
    if update.effective_user.id != OWNER_ID: return
    
    cur.execute("SELECT COUNT(*) FROM users")
    u_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM messages")
    m_count = cur.fetchone()[0]
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📊 Статистика ({m_count} msg)", callback_data="adm_stats")],
        [InlineKeyboardButton("📢 Массовая рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton("🚫 Список банов", callback_data="adm_banlist")],
        [InlineKeyboardButton("❌ Закрыть меню", callback_data="adm_close")]
    ])
    
    await update.message.reply_text(
        f"⚙️ **Админ-панель Йокосо**\n\nВсего пользователей в базе: `{u_count}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )

async def filter_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if is_banned(user.id): return

    # 1. Сначала проверяем системные кнопки
    if msg.text == "❓ Справка":
        await help_command(update, context)
        return
    if msg.text == "⚙️ Админ-панель" and user.id == OWNER_ID:
        await admin_panel_func(update, context)
        return
    if msg.text == "✉️ Отправить сообщение":
        await msg.reply_text("✍️ Жду твое сообщение (текст, фото или видео):")
        return

    # 2. Если админ в режиме рассылки
    if user.id == OWNER_ID and admin_state.get(user.id) == "waiting_broadcast":
        admin_state[user.id] = f"confirm_{msg.text}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отправить всем", callback_data="bc_yes"),
             InlineKeyboardButton("❌ Отмена", callback_data="bc_no")]
        ])
        await msg.reply_text(f"📢 **Текст для рассылки:**\n\n{msg.text}", reply_markup=kb, parse_mode="Markdown")
        return

    # 3. Если админ просто пишет (не отвечая на сообщение) — ничего не делаем
    if user.id == OWNER_ID and "reply_to" not in context.user_data:
        return

    # 4. Обработка сообщения от пользователя (создание черновика)
    m_type, file_id = "text", None
    if msg.photo: m_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video: m_type, file_id = "video", msg.video.file_id
    elif msg.voice: m_type, file_id = "voice", msg.voice.file_id

    drafts[user.id] = {"type": m_type, "content": msg.text, "file_id": file_id, "caption": msg.caption or ""}
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="confirm_send"),
         InlineKeyboardButton("✏️ Изменить", callback_data="edit_msg")]
    ])
    await update.message.reply_text("👀 Проверь сообщение перед отправкой:", reply_markup=kb)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    
    # --- Пользовательские действия ---
    if q.data == "confirm_send":
        if uid not in drafts: return
        d = drafts.pop(uid)
        mid = save_message(uid, d["type"], d["content"], d["file_id"], d["caption"])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Ответить", callback_data=f"rep_{mid}"), InlineKeyboardButton("🚫 Бан", callback_data=f"ban_{uid}")]])
        h = f"📩 Сообщение #{mid}"
        if d["type"] == "text": await context.bot.send_message(OWNER_ID, f"{h}\n\n{d['content']}", reply_markup=kb)
        elif d["type"] == "photo": await context.bot.send_photo(OWNER_ID, d["file_id"], caption=f"{h}\n{d['caption']}", reply_markup=kb)
        elif d["type"] == "video": await context.bot.send_video(OWNER_ID, d["file_id"], caption=f"{h}\n{d['caption']}", reply_markup=kb)
        await q.message.edit_text("🚀 **Доставлено!**", parse_mode="Markdown")

    elif q.data == "edit_msg":
        drafts.pop(uid, None)
        await q.message.edit_text("✏️ Черновик удален. Пришли новое сообщение:")

    # --- Админские действия ---
    elif q.data == "adm_stats":
        cur.execute("SELECT COUNT(*) FROM messages")
        m_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        u_count = cur.fetchone()[0]
        await q.message.reply_text(f"📊 **Полная статистика:**\n- Юзеров: `{u_count}`\n- Сообщений: `{m_count}`", parse_mode="Markdown")
    
    elif q.data == "adm_broadcast":
        admin_state[uid] = "waiting_broadcast"
        await q.message.reply_text("📢 Напиши текст рассылки следующим сообщением:")

    elif q.data == "bc_yes":
        state = admin_state.get(uid, "")
        if state.startswith("confirm_"):
            text = state.replace("confirm_", "")
            cur.execute("SELECT user_id FROM users WHERE is_banned=0")
            users = cur.fetchall()
            ok = 0
            for (u,) in users:
                try: 
                    await context.bot.send_message(u, f"📢 **Сообщение от администрации:**\n\n{text}", parse_mode="Markdown")
                    ok += 1
                except: continue
            await q.message.edit_text(f"✅ Успешно! Получили: `{ok}` человек.")
            admin_state.pop(uid, None)

    elif q.data.startswith("rep_"):
        context.user_data["reply_to"] = q.data.split("_")[1]
        await q.message.reply_text(f"✍️ Пиши ответ для #{context.user_data['reply_to']}:")

    elif q.data.startswith("ban_"):
        target = int(q.data.split("_")[1])
        if target == OWNER_ID: return
        cur.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (target,))
        conn.commit()
        await q.message.reply_text(f"🚫 Пользователь заблокирован.")

    elif q.data in ["adm_close", "bc_no"]:
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
            await update.message.reply_text("✅ Ответ отправлен пользователю.")
        except: await update.message.reply_text("❌ Пользователь заблокировал бота.")

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
    app.add_handler(CommandHandler("admin", admin_panel_func)) # Команда для подстраховки
    app.add_handler(CommandHandler("help", help_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Группа 1: Ответы админа
    app.add_handler(MessageHandler(filters.Chat(OWNER_ID) & ~filters.COMMAND, admin_reply_handler), group=1)
    # Группа 2: Все остальные сообщения
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, filter_incoming), group=2)
    
    app.run_polling()
