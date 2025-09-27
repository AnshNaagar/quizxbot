import os
import json
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
import aiosqlite
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DB_FILE = "quizbot.db"

# Load languages
with open("languages.json", encoding="utf-8") as f:
    LANG_DATA = json.load(f)

user_lang = {}

# --- Database init ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            question TEXT,
            options TEXT,
            answer_index INTEGER,
            FOREIGN KEY(quiz_id) REFERENCES quizzes(id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            quiz_id INTEGER,
            score INTEGER,
            total INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.commit()
    logger.info("DB initialized")

# --- Keyboards ---
def get_language_keyboard():
    kb = InlineKeyboardMarkup(row_width=4)
    for code, info in LANG_DATA.items():
        kb.insert(InlineKeyboardButton(text=info["name"], callback_data=f"lang_{code}"))
    return kb

def get_main_menu(lang_code="en"):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(text=LANG_DATA[lang_code]["create_quiz"], callback_data="create_quiz"),
        InlineKeyboardButton(text=LANG_DATA[lang_code]["take_quiz"], callback_data="take_quiz")
    )
    kb.add(
        InlineKeyboardButton(text=LANG_DATA[lang_code]["leaderboard"], callback_data="leaderboard"),
        InlineKeyboardButton(text=LANG_DATA[lang_code]["change_lang"], callback_data="change_lang")
    )
    return kb

def make_options_kb(qid, options):
    kb = InlineKeyboardMarkup()
    for i, opt in enumerate(options):
        kb.add(InlineKeyboardButton(text=opt, callback_data=f"ans|{qid}|{i}"))
    return kb

# --- Commands ---
@dp.message(lambda m: m.text == "/start")
async def cmd_start(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    if user_id not in user_lang:
        user_lang[user_id] = "en"
        await msg.answer(LANG_DATA["en"]["choose_language"], reply_markup=get_language_keyboard())
    else:
        lang_code = user_lang[user_id]
        await msg.answer(LANG_DATA[lang_code]["welcome"], reply_markup=get_main_menu(lang_code))

# --- Callbacks ---
@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    lang_code = cb.data.split("_")[1]
    user_lang[user_id] = lang_code
    await cb.message.edit_text(LANG_DATA[lang_code]["welcome"], reply_markup=get_main_menu(lang_code))

@dp.callback_query(lambda c: c.data == "change_lang")
async def change_language(cb: types.CallbackQuery):
    await cb.message.edit_text(LANG_DATA[user_lang.get(cb.from_user.id, "en")]["choose_language"],
                               reply_markup=get_language_keyboard())

@dp.callback_query(lambda c: c.data == "create_quiz")
async def create_quiz(cb: types.CallbackQuery):
    lang_code = user_lang.get(cb.from_user.id, "en")
    if cb.from_user.id not in ADMIN_IDS:
        await cb.message.answer(LANG_DATA[lang_code]["admin_only"])
        return
    await cb.message.answer(LANG_DATA[lang_code]["create_quiz_instructions"])

@dp.callback_query(lambda c: c.data == "take_quiz")
async def take_quiz(cb: types.CallbackQuery, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id, title FROM quizzes")
        quizzes = await cur.fetchall()
    if not quizzes:
        await cb.message.answer("No quizzes available yet.")
        return
    kb = InlineKeyboardMarkup()
    for q in quizzes:
        kb.add(InlineKeyboardButton(text=f"{q[1]}", callback_data=f"start_quiz|{q[0]}"))
    await cb.message.answer("Select a quiz:", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("start_quiz|"))
async def start_quiz(cb: types.CallbackQuery, state: FSMContext):
    quiz_id = int(cb.data.split("|")[1])
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id, question, options FROM questions WHERE quiz_id=?", (quiz_id,))
        rows = await cur.fetchall()
    if not rows:
        await cb.message.answer("This quiz has no questions yet.")
        return
    qlist = []
    for r in rows:
        qid, qtext, qopts = r
        opts = json.loads(qopts)
        qlist.append({"qid": qid, "text": qtext, "options": opts})
    await state.update_data(current_quiz=quiz_id, questions=qlist, pointer=0, correct=0, total=len(qlist))
    first = qlist[0]
    kb = make_options_kb(first["qid"], first["options"])
    await cb.message.answer(f"Question 1/{len(qlist)}:\n{first['text']}", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("ans|"))
async def process_answer(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split("|")
    qid = int(parts[1])
    chosen = int(parts[2])
    data = await state.get_data()
    questions = data["questions"]
    pointer = data["pointer"]
    correct = data["correct"]
    total = data["total"]
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT options, answer_index FROM questions WHERE id=?", (qid,))
        row = await cur.fetchone()
    opts = json.loads(row[0])
    ans_index = row[1]
    if chosen == ans_index:
        correct += 1
        reply_text = "✅ Correct!"
    else:
        reply_text = f"❌ Wrong! Correct: {opts[ans_index]}"
    pointer += 1
    await state.update_data(pointer=pointer, correct=correct)
    await cb.message.answer(reply_text)
    if pointer >= total:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO attempts (user_id, username, quiz_id, score, total) VALUES (?, ?, ?, ?, ?)",
                             (cb.from_user.id, cb.from_user.username or "", data["current_quiz"], correct, total))
            await db.commit()
        await cb.message.answer(f"Quiz finished! Score: {correct}/{total}")
        await state.clear()
    else:
        next_q = questions[pointer]
        kb = make_options_kb(next_q["qid"], next_q["options"])
        await cb.message.answer(f"Question {pointer+1}/{total}:\n{next_q['text']}", reply_markup=kb)

# --- Run bot ---
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
