import telebot
from telebot import types
import sqlite3
import secrets
import os
from datetime import datetime

# ====== НАСТРОЙКИ ======
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
COMMISSION = 0.05  # 5% комиссия
# =======================

bot = telebot.TeleBot(TOKEN)
user_data = {}


# ============ БАЗА ДАННЫХ ============
def db():
    conn = sqlite3.connect("/data/deals.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        reg_date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS deals (
        code TEXT PRIMARY KEY,
        seller_id INTEGER,
        buyer_id INTEGER,
        nft TEXT,
        price REAL,
        currency TEXT,
        description TEXT,
        status TEXT,
        created_at TEXT,
        paid_at TEXT,
        completed_at TEXT
    )""")
    conn.commit()
    conn.close()


def save_user(u):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, first_name, reg_date) VALUES (?,?,?,?)",
        (u.id, u.username, u.first_name, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_deal(code):
    conn = db()
    row = conn.execute("SELECT * FROM deals WHERE code=?", (code,)).fetchone()
    conn.close()
    return row


def create_deal(code, seller_id, nft, price, currency, desc):
    conn = db()
    conn.execute(
        """INSERT INTO deals (code, seller_id, nft, price, currency, description, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (code, seller_id, nft, price, currency, desc, "awaiting_payment",
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def update_deal(code, **kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [code]
    conn = db()
    conn.execute(f"UPDATE deals SET {fields} WHERE code=?", values)
    conn.commit()
    conn.close()


# ============ ХЕЛПЕРЫ ============
STATUSES = {
    "awaiting_payment": "⏳ Ожидает оплаты",
    "paid": "💰 Оплачено — продавец передаёт NFT",
    "completed": "✅ Завершена",
    "disputed": "⚠️ Спор",
    "cancelled": "❌ Отменена",
}


def main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🆕 Создать сделку", "📋 Мои сделки")
    if user_id == ADMIN_ID:
        markup.add("🛠 Админ-панель")
    return markup


def deal_card(code, viewer_id):
    d = get_deal(code)
    if not d:
        return None, None

    comm = d["price"] * COMMISSION
    seller_gets = d["price"] - comm

    text = (
        f"🆔 Сделка <b>#{d['code']}</b>\n\n"
        f"📦 NFT: {d['nft']}\n"
        f"💵 Цена: <b>{d['price']:g} {d['currency']}</b>\n"
        f"💸 Комиссия (5%): {comm:.2f} {d['currency']}\n"
        f"📥 Продавец получит: <b>{seller_gets:.2f} {d['currency']}</b>\n"
        f"📝 Описание: {d['description'] or '—'}\n"
        f"📊 Статус: {STATUSES.get(d['status'], d['status'])}"
    )

    markup = types.InlineKeyboardMarkup()

    if d["status"] == "awaiting_payment":
        markup.add(types.InlineKeyboardButton(
            "💳 Оплатить", callback_data=f"pay_{code}"))

    elif d["status"] == "paid":
        if viewer_id == d["buyer_id"]:
            markup.add(types.InlineKeyboardButton(
                "✅ Получил NFT", callback_data=f"confirm_{code}"))
            markup.add(types.InlineKeyboardButton(
                "⚠️ Открыть спор", callback_data=f"dispute_{code}"))

    return text, markup


def show_deal(chat_id, code, viewer_id):
    d = get_deal(code)
    if not d:
        bot.send_message(chat_id, "❌ Сделка не найдена.")
        return

    if d["buyer_id"] is None and viewer_id != d["seller_id"]:
        update_deal(code, buyer_id=viewer_id)

    text, markup = deal_card(code, viewer_id)
    bot.send_message(chat_id, text, parse_mode="HTML",
                     reply_markup=markup, disable_web_page_preview=True)


# ============ /start ============
@bot.message_handler(commands=['start'])
def start(message):
    save_user(message.from_user)
    args = message.text.split()

    if len(args) > 1 and args[1].startswith("deal_"):
        code = args[1].replace("deal_", "")
        show_deal(message.chat.id, code, message.from_user.id)
        return

    bot.send_message(
        message.chat.id,
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Это бот для P2P-сделок с NFT за ₽ или ⭐.\n\n"
        "• Продавец создаёт сделку и получает ссылку\n"
        "• Покупатель открывает ссылку и оплачивает\n"
        "• После подтверждения NFT считается переданным",
        reply_markup=main_menu(message.from_user.id)
    )


@bot.message_handler(commands=['admin'])
def admin_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    admin_panel(message)


# ============ СОЗДАНИЕ СДЕЛКИ ============
@bot.message_handler(func=lambda m: m.text == "🆕 Создать сделку")
def new_deal(message):
    user_data[message.from_user.id] = {}
    msg = bot.send_message(message.chat.id,
                           "🔗 Отправь ссылку на NFT (или краткое описание):")
    bot.register_next_step_handler(msg, step_nft)


def step_nft(message):
    user_data[message.from_user.id]["nft"] = message.text
    msg = bot.send_message(message.chat.id, "💵 Введи цену (число):")
    bot.register_next_step_handler(msg, step_price)


def step_price(message):
    try:
        price = float(message.text.replace(",", ".").strip())
    except ValueError:
        msg = bot.send_message(message.chat.id, "⚠️ Введи число, например: 500")
        bot.register_next_step_handler(msg, step_price)
        return
    user_data[message.from_user.id]["price"] = price

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("⭐ Звёзды", "₽ Рубли")
    msg = bot.send_message(message.chat.id, "Выбери валюту:", reply_markup=markup)
    bot.register_next_step_handler(msg, step_currency)


def step_currency(message):
    txt = message.text.lower()
    if "звезд" in txt or "⭐" in txt:
        cur = "⭐"
    elif "рубл" in txt or "₽" in txt:
        cur = "₽"
    else:
        msg = bot.send_message(message.chat.id, "Выбери: ⭐ Звёзды или ₽ Рубли")
        bot.register_next_step_handler(msg, step_currency)
        return

    user_data[message.from_user.id]["currency"] = cur
    msg = bot.send_message(
        message.chat.id,
        "📝 Краткое описание (или отправь «-», чтобы пропустить):",
        reply_markup=types.ReplyKeyboardRemove()
    )
    bot.register_next_step_handler(msg, step_desc)


def step_desc(message):
    data = user_data.pop(message.from_user.id, None)
    if not data:
        return
    desc = "" if message.text.strip() == "-" else message.text.strip()

    code = secrets.token_hex(3).upper()
    create_deal(code, message.from_user.id, data["nft"],
                data["price"], data["currency"], desc)

    me = bot.get_me()
    link = f"https://t.me/{me.username}?start=deal_{code}"
    bot.send_message(
        message.chat.id,
        f"✅ Сделка <b>#{code}</b> создана!\n\n"
        f"Отправь эту ссылку покупателю:\n{link}",
        parse_mode="HTML",
        reply_markup=main_menu(message.from_user.id),
        disable_web_page_preview=True
    )


# ============ МОИ СДЕЛКИ ============
@bot.message_handler(func=lambda m: m.text == "📋 Мои сделки")
def my_deals(message):
    conn = db()
    rows = conn.execute(
        """SELECT code, status, price, currency FROM deals
           WHERE seller_id=? OR buyer_id=?
           ORDER BY created_at DESC LIMIT 20""",
        (message.from_user.id, message.from_user.id)
    ).fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "У тебя пока нет сделок.")
        return

    lines = ["📋 Твои последние сделки:\n"]
    for r in rows:
        lines.append(
            f"• #{r['code']} — {r['price']:g} {r['currency']} — "
            f"{STATUSES.get(r['status'], r['status'])}"
        )
    bot.send_message(message.chat.id, "\n".join(lines))


# ============ АДМИНКА ============
@bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель"
                     and m.from_user.id == ADMIN_ID)
def admin_panel(message):
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    awaiting = conn.execute(
        "SELECT COUNT(*) FROM deals WHERE status='awaiting_payment'").fetchone()[0]
    paid = conn.execute(
        "SELECT COUNT(*) FROM deals WHERE status='paid'").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM deals WHERE status='completed'").fetchone()[0]
    disp = conn.execute(
        "SELECT COUNT(*) FROM deals WHERE status='disputed'").fetchone()[0]
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    bot.send_message(
        message.chat.id,
        f"🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"📦 Всего сделок: {total}\n"
        f"⏳ Ожидают оплаты: {awaiting}\n"
        f"💰 Оплачены: {paid}\n"
        f"✅ Завершены: {done}\n"
        f"⚠️ Споры: {disp}",
        parse_mode="HTML"
    )


# ============ CALLBACK-КНОПКИ ============
@bot.callback_query_handler(func=lambda c: True)
def callback(call):
    data = call.data
    uid = call.from_user.id

    if data.startswith("pay_"):
        code = data[4:]
        d = get_deal(code)
        if not d:
            bot.answer_callback_query(call.id, "Сделка не найдена")
            return
        if d["status"] != "awaiting_payment":
            bot.answer_callback_query(call.id, "Сделка уже не в статусе ожидания")
            return

        update_deal(code, status="paid", paid_at=datetime.now().isoformat())
        bot.answer_callback_query(call.id, "✅ Оплата проведена")

        text, markup = deal_card(code, uid)
        bot.edit_message_text(text, call.message.chat.id,
                              call.message.message_id,
                              parse_mode="HTML", reply_markup=markup)

        d = get_deal(code)
        try:
            bot.send_message(
                d["seller_id"],
                f"💰 Сделка #{code} <b>оплачена</b>!\n"
                f"Передай NFT покупателю и попроси подтвердить получение.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        if d["buyer_id"]:
            try:
                bot.send_message(
                    d["buyer_id"],
                    f"💰 Оплата по сделке #{code} прошла.\n"
                    f"Ожидай NFT от продавца, затем нажми «✅ Получил NFT».",
                )
            except Exception:
                pass
        return

    if data.startswith("confirm_"):
        code = data[8:]
        d = get_deal(code)
        if not d:
            bot.answer_callback_query(call.id, "Сделка не найдена")
            return
        if uid != d["buyer_id"]:
            bot.answer_callback_query(call.id, "Только покупатель может подтвердить")
            return
        if d["status"] != "paid":
            bot.answer_callback_query(call.id, "Статус не позволяет подтвердить")
            return

        update_deal(code, status="completed",
                    completed_at=datetime.now().isoformat())
        bot.answer_callback_query(call.id, "✅ Сделка завершена")

        text, markup = deal_card(code, uid)
        bot.edit_message_text(text, call.message.chat.id,
                              call.message.message_id,
                              parse_mode="HTML", reply_markup=markup)

        try:
            bot.send_message(
                d["seller_id"],
                f"✅ Покупатель подтвердил получение NFT по сделке #{code}.\n"
                f"Сделка завершена. Спасибо!"
            )
        except Exception:
            pass
        return

    if data.startswith("dispute_"):
        code = data[8:]
        d = get_deal(code)
        if not d or uid != d["buyer_id"]:
            bot.answer_callback_query(call.id, "Недоступно")
            return
        if d["status"] != "paid":
            bot.answer_callback_query(call.id, "Спор можно открыть только по оплаченной сделке")
            return

        update_deal(code, status="disputed")
        bot.answer_callback_query(call.id, "⚠️ Спор открыт, админ свяжется")

        text, markup = deal_card(code, uid)
        bot.edit_message_text(text, call.message.chat.id,
                              call.message.message_id,
                              parse_mode="HTML", reply_markup=markup)

        try:
            bot.send_message(
                ADMIN_ID,
                f"⚠️ <b>СПОР</b> по сделке #{code}\n"
                f"Продавец: <code>{d['seller_id']}</code>\n"
                f"Покупатель: <code>{d['buyer_id']}</code>\n"
                f"Сумма: {d['price']:g} {d['currency']}\n"
                f"NFT: {d['nft']}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    bot.answer_callback_query(call.id, "Неизвестное действие")


# ============ ЗАПУСК ============
if __name__ == "__main__":
    init_db()
    print("Бот запущен...")
    bot.infinity_polling()
