import os
import json
import asyncio
import aiosqlite
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
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

# Load language data
with open("languages.json","r",encoding="utf-8") as f:
    LANG_DATA = json.load(f)

user_lang = {}  # store user language

# --- Database init ---
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
    logger.info("DB initialized")

# --- Keyboards ---
def get_language_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANG_DATA[lang]["language_name"], callback_data=f"lang_{lang}") for lang in ["en","hi","es","fr","de"]]
    ])
    return kb

def get_main_menu(lang_code="en"):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANG_DATA[lang_code]["create_quiz"], callback_data="create_quiz")],
        [InlineKeyboardButton(text=LANG_DATA[lang_code]["language"], callback_data="change_lang")],
        [InlineKeyboardButton(text="Take Quiz", callback_data="take_quiz")],
        [InlineKeyboardButton(text="Leaderboard", callback_data="leaderboard")]
    ])
    return kb

def make_options_kb(qid, options):
    kb = InlineKeyboardMarkup()
    for i,opt in enumerate(options):
        kb.add(InlineKeyboardButton(text=opt, callback_data=f"ans|{qid}|{i}"))
    return kb

# --- Start ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    user_id = msg.from_user.id
    if user_id not in user_lang:
        user_lang[user_id] = "en"
        await msg.answer(LANG_DATA["en"]["choose_language"], reply_markup=get_language_keyboard())
    else:
        lang_code = user_lang[user_id]
        await msg.answer(LANG_DATA[lang_code]["welcome"], reply_markup=get_main_menu(lang_code))

# --- Language selection ---
@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(cb: types.CallbackQuery):
    lang_code = cb.data.split("_")[1]
    user_lang[cb.from_user.id] = lang_code
    await cb.message.edit_text(LANG_DATA[lang_code]["welcome"], reply_markup=get_main_menu(lang_code))

@dp.callback_query(lambda c: c.data=="change_lang")
async def change_language(cb: types.CallbackQuery):
    lang_code = user_lang.get(cb.from_user.id,"en")
    await cb.message.edit_text(LANG_DATA[lang_code]["choose_language"], reply_markup=get_language_keyboard())

# --- Create quiz ---
@dp.callback_query(lambda c: c.data=="create_quiz")
async def callback_create_quiz(cb: types.CallbackQuery):
    lang_code = user_lang.get(cb.from_user.id,"en")
    if cb.from_user.id not in ADMIN_IDS:
        await cb.message.answer("Only admins can create quizzes.")
        return
    await cb.message.answer(LANG_DATA[lang_code]["create_quiz_instructions"])

# --- Add question ---
@dp.message(Command("create_quiz"))
async def cmd_create_quiz(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("Only admins.")
        return
    title = msg.get_args()
    if not title:
        await msg.reply("Usage: /create_quiz <Quiz Title>")
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO quizzes(title) VALUES(?)",(title,))
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        r = await cur.fetchone()
    quiz_id = r[0] if r else "?"
    await msg.reply(f"Quiz created with id {quiz_id}. Add questions with:\n/add_question {quiz_id} | Question text | opt1 ; opt2 | answer_index(0-based)")

@dp.message(Command("add_question"))
async def cmd_add_question(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("Only admins.")
        return
    raw = msg.get_args()
    try:
        parts = [p.strip() for p in raw.split("|")]
        quiz_id = int(parts[0])
        question = parts[1]
        options = [o.strip() for o in parts[2].split(";") if o.strip()]
        answer_index = int(parts[3])
        if len(options)<2:
            await msg.reply("Minimum 2 options required!")
            return
    except:
        await msg.reply("Format error. Example:\n/add_question 1 | What is 2+2? | 1 ; 2 ; 4 | 2")
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO questions(quiz_id,question,options,answer_index) VALUES(?,?,?,?)",
                         (quiz_id,question,json.dumps(options),answer_index))
        await db.commit()
    await msg.reply("Question added!")

# --- Take quiz ---
@dp.callback_query(lambda c: c.data=="take_quiz")
async def callback_take_quiz(cb: types.CallbackQuery, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id,title FROM quizzes")
        rows = await cur.fetchall()
    if not rows:
        await cb.message.answer("No quizzes available yet.")
        return
    text = "Available quizzes:\n" + "\n".join([f"{r[0]} - {r[1]}" for r in rows])
    await cb.message.answer(text+"\nUse /take <quiz_id> to start a quiz")

@dp.message(Command("take"))
async def cmd_take(msg: types.Message, state: FSMContext):
    qid = msg.get_args()
    if not qid.isdigit():
        await msg.reply("Quiz ID must be a number.")
        return
    qid = int(qid)
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT title FROM quizzes WHERE id=?",(qid,))
        quiz = await cur.fetchone()
        if not quiz:
            await msg.reply("Quiz not found")
            return
        cur = await db.execute("SELECT id,question,options,answer_index FROM questions WHERE quiz_id=?",(qid,))
        rows = await cur.fetchall()
    if not rows:
        await msg.reply("Quiz has no questions.")
        return
    questions=[]
    for r in rows:
        questions.append({"qid":r[0],"text":r[1],"options":json.loads(r[2]),"answer_index":r[3]})
    await state.update_data(current_quiz=qid,questions=questions,pointer=0,correct=0,total=len(questions))
    first = questions[0]
    await msg.reply(f"Starting quiz: {quiz[0]}\nQuestion 1/{len(questions)}:\n{first['text']}", reply_markup=make_options_kb(first["qid"],first["options"]))

# --- Answer callback ---
@dp.callback_query(lambda c: c.data.startswith("ans|"))
async def process_answer(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split("|")
    qid = int(parts[1]); chosen=int(parts[2])
    data = await state.get_data()
    questions = data.get("questions",[]); pointer = data.get("pointer",0); correct = data.get("correct",0)
    question = next((q for q in questions if q["qid"]==qid),None)
    if chosen == question["answer_index"]:
        correct+=1
        reply="✅ Correct!"
    else:
        reply=f"❌ Wrong. Correct: {question['options'][question['answer_index']]}"
    pointer+=1
    await state.update_data(pointer=pointer,correct=correct)
    await cb.message.answer(reply)
    if pointer>=len(questions):
        quiz_id = data.get("current_quiz")
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO attempts(user_id,username,quiz_id,score,total) VALUES(?,?,?,?,?)",
                             (cb.from_user.id,cb.from_user.username or "",quiz_id,correct,len(questions)))
            await db.commit()
        await cb.message.answer(f"Quiz finished! Score: {correct}/{len(questions)}")
        await state.clear()
    else:
        next_q = questions[pointer]
        await cb.message.answer(f"Question {pointer+1}/{len(questions)}:\n{next_q['text']}", reply_markup=make_options_kb(next_q["qid"],next_q["options"]))

# --- Leaderboard ---
@dp.callback_query(lambda c: c.data=="leaderboard")
async def callback_leaderboard(cb: types.CallbackQuery):
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

if __name__=="__main__":
    asyncio.run(on_startup())
    from aiogram import executor
    executor.start_polling(dp)
