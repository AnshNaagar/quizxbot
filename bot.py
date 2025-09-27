import os
import logging
import asyncio
import json
from aiogram import Bot, Dispatcher, types
from aiogram.types import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import aiosqlite
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)

DB_FILE = "quizbot.db"

# Load 20-language data
with open("languages.json", "r", encoding="utf-8") as f:
    LANG_DATA = json.load(f)

# In-memory user language store
user_lang = {}

### --- FSM State for quiz ---
class QuizStates(StatesGroup):
    in_quiz = State()

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
        [InlineKeyboardButton(text=LANG_DATA[lang_code].get("create_quiz","Create Quiz"), callback_data="create_quiz")],
        [InlineKeyboardButton(text=LANG_DATA[lang_code].get("language","Change Language"), callback_data="change_lang")]
    ])
    return kb

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
        await msg.reply(LANG_DATA["en"].get("choose_language", "Choose your language:"), reply_markup=get_language_keyboard())
    else:
        await msg.reply(LANG_DATA[user_lang[user_id]].get("welcome","Welcome!"), reply_markup=get_main_menu(user_id))

@dp.callback_query_handler(lambda c: c.data.startswith("lang_"))
async def set_language(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    lang_code = cb.data.split("_")[1]
    user_lang[user_id] = lang_code
    await cb.message.edit_text(LANG_DATA[lang_code].get("welcome","Welcome!"), reply_markup=get_main_menu(user_id))
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data == "change_lang")
async def change_language(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    await cb.message.edit_text(LANG_DATA[user_lang.get(user_id,"en")].get("choose_language","Choose your language:"), reply_markup=get_language_keyboard())
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data == "create_quiz")
async def callback_create_quiz(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    lang_code = user_lang.get(user_id,"en")
    instructions = LANG_DATA[lang_code].get(
        "create_quiz_instructions",
        "Use /create_quiz <Quiz Title> and /add_question to add questions."
    )
    await cb.message.answer(instructions)
    await cb.answer()

### --- Create quiz ---
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

@dp.message_handler(commands=["add_question"])
async def cmd_add_question(msg: types.Message):
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
        await msg.reply("Failed to parse. Example:\n/add_question 1 | What is 2+2? | 1 ; 2 ; 4 ; 3 | 2")
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO questions (quiz_id, question, options, answer_index) VALUES (?, ?, ?, ?)",
                         (quiz_id, question, json.dumps(options), answer_index))
        await db.commit()
    await msg.reply("✅ Question added successfully.")

### --- Take quiz (min 10 quizzes required) ---
@dp.message_handler(commands=["take"])
async def cmd_take(msg: types.Message):
    user_id = msg.from_user.id
    args = msg.get_args().strip()
    if not args:
        await msg.reply("Usage: /take <quiz_id>")
        return
    quiz_id = int(args)

    # Check min 10 quizzes for user
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT COUNT(*) FROM quizzes WHERE user_id=?", (user_id,))
        quiz_count = (await cur.fetchone())[0]
    if quiz_count < 10:
        await msg.reply(f"❌ You must create at least 10 quizzes before taking any quiz. Currently: {quiz_count}")
        return

    # Load quiz questions
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT title FROM quizzes WHERE id=?", (quiz_id,))
        quiz = await cur.fetchone()
        if not quiz:
            await msg.reply("Quiz not found.")
            return
        cur = await db.execute("SELECT id, question, options, answer_index FROM questions WHERE quiz_id=?", (quiz_id,))
        rows = await cur.fetchall()
    if not rows:
        await msg.reply("This quiz has no questions yet.")
        return

    # Prepare question list
    qlist = []
    for r in rows:
        qid, qtext, qopts, ans_index = r
        qlist.append({"qid": qid, "text": qtext, "options": json.loads(qopts), "answer_index": ans_index})

    # Save in FSM state
    state = dp.current_state(chat=msg.chat.id, user=user_id)
    await state.update_data(current_quiz=quiz_id, questions=qlist, pointer=0, correct=0, total=len(qlist))

    # Send first question
    first = qlist[0]
    kb = make_options_kb(first["qid"], first["options"])
    await msg.reply(f"Starting quiz: *{quiz[0]}*\nQuestion 1/{len(qlist)}:\n{first['text']}", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    await QuizStates.in_quiz.set()

### --- Answer callback ---
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("ans|"), state=QuizStates.in_quiz)
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

    # Current question
    cur_q = questions[pointer]
    is_correct = (chosen == cur_q["answer_index"])
    if is_correct:
        correct += 1
    pointer += 1

    await state.update_data(pointer=pointer, correct=correct)

    # Respond to user
    lang_code = user_lang.get(cb.from_user.id,"en")
    msg_text = LANG_DATA[lang_code].get("correct","✅ Correct!") if is_correct else \
               f"{LANG_DATA[lang_code].get('wrong','❌ Wrong.')} {cur_q['options'][cur_q['answer_index']]}"
    await cb.message.reply(msg_text)

    # Next question or finish
    if pointer >= total:
        # Save attempt
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO attempts (user_id, username, quiz_id, score, total) VALUES (?, ?, ?, ?, ?)",
                             (cb.from_user.id, cb.from_user.username or "", data["current_quiz"], correct, total))
            await db.commit()
        await cb.message.reply(f"Quiz finished! Your score: *{correct}/{total}*", parse_mode=ParseMode.MARKDOWN)
        await state.finish()
    else:
        next_q = questions[pointer]
        kb = make_options_kb(next_q["qid"], next_q["options"])
        await cb.message.reply(f"Question {pointer+1}/{total}:\n{next_q['text']}", reply_markup=kb)

### --- Startup ---
async def on_startup(dp):
    await init_db()
    logger.info("DB initialized.")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
