import os
import logging
import asyncio
import json
from aiogram import Bot, Dispatcher, types
from aiogram.types import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import aiosqlite
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

DB_FILE = "quizbot.db"

# Load 20-language data
with open("languages.json", "r", encoding="utf-8") as f:
    LANG_DATA = json.load(f)

# In-memory user language store
user_lang = {}

### --- Database helpers ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER,
                question TEXT,
                options TEXT,
                answer_index INTEGER,
                FOREIGN KEY(quiz_id) REFERENCES quizzes(id)
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                quiz_id INTEGER,
                score INTEGER,
                total INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

### --- Language keyboards ---
def get_language_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="English", callback_data="lang_en"),
         InlineKeyboardButton(text="हिंदी", callback_data="lang_hi"),
         InlineKeyboardButton(text="Español", callback_data="lang_es"),
         InlineKeyboardButton(text="Français", callback_data="lang_fr")],
        [InlineKeyboardButton(text="Deutsch", callback_data="lang_de"),
         InlineKeyboardButton(text="中文", callback_data="lang_zh"),
         InlineKeyboardButton(text="العربية", callback_data="lang_ar"),
         InlineKeyboardButton(text="Русский", callback_data="lang_ru")],
        [InlineKeyboardButton(text="日本語", callback_data="lang_ja"),
         InlineKeyboardButton(text="Português", callback_data="lang_pt"),
         InlineKeyboardButton(text="বাংলা", callback_data="lang_bn"),
         InlineKeyboardButton(text="ਪੰਜਾਬੀ", callback_data="lang_pa")],
        [InlineKeyboardButton(text="한국어", callback_data="lang_ko"),
         InlineKeyboardButton(text="Italiano", callback_data="lang_it"),
         InlineKeyboardButton(text="Türkçe", callback_data="lang_tr"),
         InlineKeyboardButton(text="Tiếng Việt", callback_data="lang_vi")],
        [InlineKeyboardButton(text="ไทย", callback_data="lang_th"),
         InlineKeyboardButton(text="Bahasa Indonesia", callback_data="lang_id"),
         InlineKeyboardButton(text="فارسی", callback_data="lang_fa"),
         InlineKeyboardButton(text="اردو", callback_data="lang_ur")]
    ])
    return kb

def get_main_menu(user_id):
    lang_code = user_lang.get(user_id, "en")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANG_DATA[lang_code]["create_quiz"], callback_data="create_quiz")],
        [InlineKeyboardButton(text=LANG_DATA[lang_code]["language"], callback_data="change_lang")]
    ])
    return kb

### --- Option keyboard for quiz questions ---
def make_options_kb(qid, options):
    kb = InlineKeyboardMarkup()
    for i, opt in enumerate(options):
        kb.add(InlineKeyboardButton(text=opt, callback_data=f"ans|{qid}|{i}"))
    return kb

### --- Start / Language selection ---
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    if user_id not in user_lang:
        user_lang[user_id] = "en"
        await msg.reply(LANG_DATA["en"]["choose_language"], reply_markup=get_language_keyboard())
    else:
        await msg.reply(LANG_DATA[user_lang[user_id]]["welcome"], reply_markup=get_main_menu(user_id))

@dp.callback_query_handler(lambda c: c.data.startswith("lang_"))
async def set_language(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    lang_code = cb.data.split("_")[1]
    user_lang[user_id] = lang_code
    await cb.message.edit_text(LANG_DATA[lang_code]["welcome"], reply_markup=get_main_menu(user_id))

@dp.callback_query_handler(lambda c: c.data == "change_lang")
async def change_language(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    await cb.message.edit_text(LANG_DATA[user_lang.get(user_id, "en")]["choose_language"], reply_markup=get_language_keyboard())

### --- Create quiz (unlimited quizzes) ---
@dp.message_handler(commands=["create_quiz"])
async def cmd_create_quiz(msg: types.Message):
    user_id = msg.from_user.id
    args = msg.get_args().strip()
    if not args:
        await msg.reply("Usage: /create_quiz <Quiz Title>")
        return

    title = args
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO quizzes (user_id, title) VALUES (?, ?)", (user_id, title))
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        quiz_id = (await cur.fetchone())[0]
    await msg.reply(f"✅ Quiz created with ID {quiz_id}. Add questions using:\n"
                    f"/add_question {quiz_id} | Question text | opt1 ; opt2 ; ... | answer_index(0-based)\n"
                    f"Each question must have at least 2 options.")

### --- Add question with at least 2 options ---
@dp.message_handler(commands=["add_question"])
async def cmd_add_question(msg: types.Message):
    user_id = msg.from_user.id
    raw = msg.get_args().strip()
    if not raw:
        await msg.reply("Usage:\n/add_question <quiz_id> | question text | opt1 ; opt2 ; ... | answer_index(0-based)")
        return
    try:
        parts = [p.strip() for p in raw.split("|")]
        quiz_id = int(parts[0])
        question = parts[1]
        options = [o.strip() for o in parts[2].split(";") if o.strip()]
        answer_index = int(parts[3])
        if len(options) < 2:
            await msg.reply("❌ Each question must have at least 2 options.")
            return
    except:
        await msg.reply("Failed to parse. Make sure format is correct.\nExample:\n"
                        "/add_question 1 | What is 2+2? | 1 ; 2 ; 4 ; 3 | 2")
        return

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO questions (quiz_id, question, options, answer_index) VALUES (?, ?, ?, ?)",
                         (quiz_id, question, json.dumps(options), answer_index))
        await db.commit()
    await msg.reply("✅ Question added successfully.")

### --- Take quiz: require min 10 quizzes ---
@dp.message_handler(commands=["take"])
async def cmd_take(msg: types.Message):
    user_id = msg.from_user.id
    args = msg.get_args().strip()
    if not args:
        await msg.reply("Usage: /take <quiz_id>")
        return

    # Check if user has at least 10 quizzes
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT COUNT(*) FROM quizzes WHERE user_id=?", (user_id,))
        quiz_count = (await cur.fetchone())[0]
    if quiz_count < 10:
        await msg.reply(f"❌ You must create at least 10 quizzes before taking any quiz. Currently: {quiz_count}")
        return

    # existing take quiz logic goes here...
    # load quiz, questions, options, manage state, handle answers etc.

### --- Startup ---
async def on_startup(dp):
    await init_db()
    logger.info("DB initialized.")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
