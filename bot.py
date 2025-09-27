import os
import json
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage, bot=bot)

DB_FILE = "quizbot.db"

# Load languages
with open("languages.json","r",encoding="utf-8") as f:
    LANG_DATA = json.load(f)

# Store user language preference
user_lang = {}

# --- FSM for quiz ---
class QuizStates(StatesGroup):
    in_quiz = State()

# --- Helper keyboards ---
def get_language_kb():
    kb = InlineKeyboardMarkup(row_width=4)
    for code, lang in LANG_DATA.items():
        kb.insert(InlineKeyboardButton(text=lang.get("create_quiz","❓"), callback_data=f"lang_{code}"))
    return kb

def get_main_menu(lang_code="en"):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(text=LANG_DATA[lang_code]["create_quiz"], callback_data="create_quiz"),
        InlineKeyboardButton(text=LANG_DATA[lang_code]["language"], callback_data="change_lang")
    )
    kb.add(
        InlineKeyboardButton(text="Take Quiz", callback_data="take_quiz"),
        InlineKeyboardButton(text="My Score", callback_data="my_score")
    )
    kb.add(
        InlineKeyboardButton(text="Leaderboard", callback_data="leaderboard")
    )
    return kb

def make_options_kb(qid, options):
    kb = InlineKeyboardMarkup()
    for i,opt in enumerate(options):
        kb.add(InlineKeyboardButton(text=opt, callback_data=f"ans|{qid}|{i}"))
    return kb

# --- DB setup ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

# --- Start command ---
@dp.message(CommandStart())
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    if user_id not in user_lang:
        user_lang[user_id] = "en"
        await msg.answer(LANG_DATA["en"]["choose_language"], reply_markup=get_language_kb())
    else:
        lang_code = user_lang[user_id]
        await msg.answer(LANG_DATA[lang_code]["welcome"], reply_markup=get_main_menu(lang_code))

# --- Language selection ---
@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    lang_code = cb.data.split("_")[1]
    user_lang[user_id] = lang_code
    await cb.message.edit_text(
        LANG_DATA[lang_code]["welcome"],
        reply_markup=get_main_menu(lang_code)
    )

# --- Change language ---
@dp.callback_query(lambda c: c.data=="change_lang")
async def change_language(cb: types.CallbackQuery):
    lang_code = user_lang.get(cb.from_user.id,"en")
    await cb.message.edit_text(
        LANG_DATA[lang_code]["choose_language"],
        reply_markup=get_language_kb()
    )

# --- Create quiz (admin only) ---
@dp.callback_query(lambda c: c.data=="create_quiz")
async def callback_create_quiz(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    if user_id not in ADMIN_IDS:
        await cb.message.answer("Only admins can create quizzes.")
        return
    lang_code = user_lang.get(user_id,"en")
    await cb.message.answer(LANG_DATA[lang_code]["create_quiz_instructions"])

# --- Add question (admin only) ---
@dp.message(lambda m: m.text.startswith("/create_quiz") or m.text.startswith("/add_question"))
async def admin_quiz_commands(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("Only admins can use this command.")
        return
    args = msg.get_args()
    if msg.text.startswith("/create_quiz"):
        if not args:
            await msg.reply("Usage: /create_quiz <Title>")
            return
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO quizzes (title) VALUES (?)",(args,))
            await db.commit()
        await msg.reply("Quiz created. Add questions using /add_question <quiz_id> | question | opt1 ; opt2 | answer_index")
    elif msg.text.startswith("/add_question"):
        try:
            parts = [p.strip() for p in args.split("|")]
            quiz_id = int(parts[0])
            question = parts[1]
            options = [o.strip() for o in parts[2].split(";") if o.strip()]
            answer_index = int(parts[3])
            if len(options)<2:
                await msg.reply("Each question must have at least 2 options.")
                return
        except:
            await msg.reply("Invalid format. /add_question <quiz_id> | question | opt1 ; opt2 | answer_index")
            return
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO questions (quiz_id, question, options, answer_index) VALUES (?, ?, ?, ?)",
                             (quiz_id, question, json.dumps(options), answer_index))
            await db.commit()
        await msg.reply("Question added successfully.")

# --- Take quiz ---
@dp.callback_query(lambda c: c.data=="take_quiz")
async def callback_take_quiz(cb: types.CallbackQuery, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id, title FROM quizzes")
        quizzes = await cur.fetchall()
    if len(quizzes)<10:
        await cb.message.answer("At least 10 quizzes must be created before taking a quiz.")
        return
    kb = InlineKeyboardMarkup()
    for q in quizzes:
        kb.add(InlineKeyboardButton(text=q[1], callback_data=f"quizid_{q[0]}"))
    await cb.message.answer("Select a quiz:", reply_markup=kb)

# --- Start selected quiz ---
@dp.callback_query(lambda c: c.data.startswith("quizid_"))
async def start_quiz(cb: types.CallbackQuery, state: FSMContext):
    quiz_id = int(cb.data.split("_")[1])
    user_id = cb.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id, question, options, answer_index FROM questions WHERE quiz_id=?",(quiz_id,))
        rows = await cur.fetchall()
    if not rows:
        await cb.message.answer("This quiz has no questions yet.")
        return
    questions = [{"qid":r[0], "text":r[1], "options":json.loads(r[2]), "answer_index":r[3]} for r in rows]
    await state.update_data(questions=questions, pointer=0, correct=0, current_quiz=quiz_id)
    first = questions[0]
    await cb.message.answer(f"Question 1/{len(questions)}:\n{first['text']}", reply_markup=make_options_kb(first["qid"], first["options"]))
    await QuizStates.in_quiz.set()

# --- Answer handler ---
@dp.callback_query(lambda c: c.data.startswith("ans|"), state=QuizStates.in_quiz)
async def answer_question(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split("|")
    qid, chosen = int(parts[1]), int(parts[2])
    data = await state.get_data()
    questions = data["questions"]
    pointer = data["pointer"]
    correct = data["correct"]
    total = len(questions)
    cur_q = questions[pointer]
    is_correct = (chosen==cur_q["answer_index"])
    if is_correct:
        correct+=1
    pointer+=1
    await state.update_data(pointer=pointer, correct=correct)
    lang_code = user_lang.get(cb.from_user.id,"en")
    msg_text = LANG_DATA[lang_code]["correct"] if is_correct else f"{LANG_DATA[lang_code]['wrong']} {cur_q['options'][cur_q['answer_index']]}"
    await cb.message.answer(msg_text)
    if pointer>=total:
        # Save attempt
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO attempts (user_id, username, quiz_id, score, total) VALUES (?,?,?,?,?)",
                             (cb.from_user.id, cb.from_user.username or "", data["current_quiz"], correct, total))
            await db.commit()
        await cb.message.answer(f"Quiz finished! Your score: {correct}/{total}")
        await state.clear()
    else:
        next_q = questions[pointer]
        await cb.message.answer(f"Question {pointer+1}/{total}:\n{next_q['text']}", reply_markup=make_options_kb(next_q["qid"], next_q["options"]))

# --- Scores ---
@dp.callback_query(lambda c: c.data=="my_score")
async def my_score(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT quiz_id, score, total, timestamp FROM attempts WHERE user_id=? ORDER BY timestamp DESC LIMIT 10",(cb.from_user.id,))
        rows = await cur.fetchall()
    if not rows:
        await cb.message.answer("You have no attempts yet.")
        return
    text = "\n".join([f"Quiz {r[0]}: {r[1]}/{r[2]} on {r[3]}" for r in rows])
    await cb.message.answer(f"Your recent attempts:\n{text}")

# --- Leaderboard ---
@dp.callback_query(lambda c: c.data=="leaderboard")
async def leaderboard(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("""
            SELECT username, AVG(score*1.0/total) as pct, COUNT(*) as attempts
            FROM attempts
            GROUP BY user_id
            HAVING attempts>0
            ORDER BY pct DESC
            LIMIT 10
        """)
        rows = await cur.fetchall()
    if not rows:
        await cb.message.answer("No leaderboard yet.")
        return
    text=""
    for i,r in enumerate(rows,1):
        text+=f"{i}. {r[0] or 'Unknown'} — {round(r[1]*100,2)}% ({r[2]} attempts)\n"
    await cb.message.answer(f"Top performers:\n{text}")

# --- Startup ---
async def on_startup():
    await init_db()
    logger.info("DB initialized.")

if __name__=="__main__":
    asyncio.run(on_startup())
    from aiogram import executor
    executor.start_polling(dp)
