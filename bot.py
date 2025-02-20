import telebot
import sqlite3
import pytz
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import os

# --- Инициализация базы данных ---
conn = sqlite3.connect("mood_tracker.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT DEFAULT 'UTC',
    start_hour INTEGER DEFAULT 8,
    end_hour INTEGER DEFAULT 22,
    interval_hours INTEGER DEFAULT 3
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    end_time DATETIME,
    duration INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    question_id INTEGER,
    answer TEXT,
    start_time DATETIME,
    end_time DATETIME,
    duration INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    order_num INTEGER UNIQUE NOT NULL
)
""")

conn.commit()

# --- Заполнение базы данных вопросами ---
def seed_questions():
    questions = [
        ("Что я сейчас чувствую? Какие эмоции испытываю?", 1),
        ("Что происходит с моим телом, когда я ощущаю это чувство?", 2),
        ("Что я хотел бы изменить? Что я хочу на самом деле?", 3),
        ("Что еще я чувствую?", 4),
        ("Кому адресовано это чувство?", 5),
        ("Как я могу донести свои эмоции и мысли адресату?", 6)
    ]
    cursor.executemany("INSERT OR IGNORE INTO questions (text, order_num) VALUES (?, ?)", questions)
    conn.commit()

seed_questions()

# --- Получение токена из переменной окружения ---
TOKEN = os.getenv("TOKEN")
bot = telebot.TeleBot(TOKEN)
scheduler = BackgroundScheduler()
scheduler.start()

# --- Функции работы с базой данных ---

def save_user_settings(user_id, timezone="UTC", start_hour=8, end_hour=22, interval=3):
    cursor.execute("""
    INSERT INTO users (user_id, timezone, start_hour, end_hour, interval_hours)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET timezone=?, start_hour=?, end_hour=?, interval_hours=?
    """, (user_id, timezone, start_hour, end_hour, interval, timezone, start_hour, end_hour, interval))
    conn.commit()

def get_user_settings(user_id):
    cursor.execute("SELECT timezone, start_hour, end_hour, interval_hours FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def start_session(user_id):
    cursor.execute("INSERT INTO sessions (user_id, start_time) VALUES (?, CURRENT_TIMESTAMP)", (user_id,))
    conn.commit()
    return cursor.lastrowid

def end_session(session_id):
    cursor.execute("""
    UPDATE sessions
    SET end_time = CURRENT_TIMESTAMP,
        duration = (strftime('%s', CURRENT_TIMESTAMP) - strftime('%s', start_time))
    WHERE session_id = ?
    """, (session_id,))
    conn.commit()

def save_response(session_id, question_id, answer, start_time, end_time):
    duration = (end_time - start_time).seconds
    cursor.execute("""
    INSERT INTO responses (session_id, question_id, answer, start_time, end_time, duration)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, question_id, answer, start_time, end_time, duration))
    conn.commit()

def ask_next_question(user_id, session_id, current_order=0):
    cursor.execute("SELECT id, text FROM questions WHERE active=1 AND order_num > ? ORDER BY order_num LIMIT 1",
                   (current_order,))
    next_question = cursor.fetchone()

    if next_question:
        question_id, question_text = next_question
        bot.send_message(user_id, question_text)
        start_time = datetime.now()
        bot.register_next_step_handler_by_chat_id(user_id, lambda message: save_answer_and_continue(user_id, session_id,
                                                                                                    question_id,
                                                                                                    message,
                                                                                                    start_time))
    else:
        end_session(session_id)
        bot.send_message(user_id, "Спасибо за ответы!")

def save_answer_and_continue(user_id, session_id, question_id, message, start_time):
    end_time = datetime.now()
    save_response(session_id, question_id, message.text, start_time, end_time)
    ask_next_question(user_id, session_id, question_id)

def ask_questions(user_id, force=False):
    user_settings = get_user_settings(user_id)
    if not user_settings:
        return

    timezone, start_hour, end_hour, interval = user_settings
    user_tz = pytz.timezone(timezone)
    current_hour = datetime.now(user_tz).hour

    if force or (start_hour <= current_hour < end_hour):
        session_id = start_session(user_id)
        ask_next_question(user_id, session_id)
    else:
        print(f"Пропускаем опрос для {user_id}, так как сейчас {current_hour}:00 в его часовом поясе.")

# --- Обработчики команд ---
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.send_message(message.chat.id, "Привет! 😊 Я помогу тебе отслеживать настроение!\n\nВот что я умею:\n📌 /settings – Настроить удобное время опросов.\n📌 /ask – Начать сессию вопросов прямо сейчас!\n\nПопробуй! 🚀")

@bot.message_handler(commands=['settings'])
def settings_message(message):
    bot.send_message(message.chat.id,
                     "Отправь настройки в формате: Часовой пояс, Час начала, Час окончания, Интервал (в часах).\nПример: Asia/Yekaterinburg, 8, 22, 3\n\nСписок доступных часовых поясов: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones")

@bot.message_handler(func=lambda message: "," in message.text)
def save_settings(message):
    try:
        user_id = message.chat.id
        timezone, start_hour, end_hour, interval = message.text.split(",")
        save_user_settings(user_id, timezone.strip(), int(start_hour), int(end_hour), int(interval))
        bot.send_message(user_id, "✅ Настройки сохранены! Теперь бот будет задавать вопросы по твоему расписанию.")
    except Exception as e:
        bot.send_message(user_id, f"⚠️ Ошибка в формате ввода: {e}")

@bot.message_handler(commands=['ask'])
def manual_question(message):
    ask_questions(message.chat.id, force=True)

# --- Запуск бота ---
bot.polling()
