import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import ParseMode, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import aiosqlite
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

DB_FILE = "quizbot.db"

### --- Database helpers ---
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
                options TEXT,       -- JSON stringified list
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

### --- Utility: create inline keyboard for options ---
def make_options_kb(qid, options):
    kb = InlineKeyboardMarkup()
    for i, opt in enumerate(options):
        kb.add(InlineKeyboardButton(text=opt, callback_data=f"ans|{qid}|{i}"))
    return kb

### --- Admin commands: create quiz, add question ---
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    text = ("Welcome to QuizBot!\n\n"
            "/take <quiz_id> - take a quiz\n"
            "/list - list available quizzes\n"
            "/myscore - see your recent attempts\n\n"
            "Admins: /create_quiz, /add_question")
    await msg.reply(text)

@dp.message_handler(commands=["list"])
async def cmd_list(msg: types.Message):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id, title FROM quizzes")
        rows = await cur.fetchall()
    if not rows:
        await msg.reply("No quizzes yet. Admins can add quizzes with /create_quiz")
        return
    text = "Available quizzes:\n" + "\n".join([f"{r[0]} — {r[1]}" for r in rows])
    await msg.reply(text)

@dp.message_handler(commands=["create_quiz"])
async def cmd_create_quiz(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("Only admins can create quizzes.")
        return
    # Usage: /create_quiz Quiz Title Here
    args = msg.get_args()
    if not args:
        await msg.reply("Usage: /create_quiz <Quiz Title>")
        return
    title = args.strip()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO quizzes (title) VALUES (?)", (title,))
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        r = await cur.fetchone()
    quiz_id = r[0] if r else "?"
    await msg.reply(f"Quiz created with id {quiz_id}. Add questions with:\n/add_question {quiz_id} | question text | opt1 ; opt2 ; opt3 ; opt4 | answer_index(0-based)")

@dp.message_handler(commands=["add_question"])
async def cmd_add_question(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("Only admins can add questions.")
        return
    # Expected format:
    # /add_question <quiz_id> | question text | opt1 ; opt2 ; opt3 | answer_index
    raw = msg.get_args()
    if not raw:
        await msg.reply("Usage:\n/add_question <quiz_id> | question text | opt1 ; opt2 ; ... | answer_index(0-based)")
        return
    try:
        parts = [p.strip() for p in raw.split("|")]
        quiz_id = int(parts[0])
        question = parts[1]
        options = [o.strip() for o in parts[2].split(";") if o.strip()]
        ans_index = int(parts[3])
    except Exception as e:
        await msg.reply("Failed to parse. Make sure format is correct.\nExample:\n/add_question 1 | What is 2+2? | 1 ; 2 ; 4 ; 3 | 2")
        return
    import json
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO questions (quiz_id, question, options, answer_index) VALUES (?, ?, ?, ?)",
                         (quiz_id, question, json.dumps(options), ans_index))
        await db.commit()
    await msg.reply("Question added.")

### --- Taking quiz ---
@dp.message_handler(commands=["take"])
async def cmd_take(msg: types.Message):
    # /take <quiz_id>
    args = msg.get_args().strip()
    if not args:
        await msg.reply("Usage: /take <quiz_id>")
        return
    try:
        quiz_id = int(args)
    except:
        await msg.reply("Quiz id must be a number.")
        return
    # load questions
    import json
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT title FROM quizzes WHERE id=?", (quiz_id,))
        quiz = await cur.fetchone()
        if not quiz:
            await msg.reply("Quiz not found.")
            return
        cur = await db.execute("SELECT id, question, options FROM questions WHERE quiz_id=?", (quiz_id,))
        rows = await cur.fetchall()
    if not rows:
        await msg.reply("This quiz has no questions yet.")
        return
    # We'll store state in-memory per user for a simple run (could persist)
    user_key = (msg.from_user.id, quiz_id)
    # prepare questions list
    qlist = []
    for r in rows:
        qid, qtext, qopts = r
        opts = json.loads(qopts)
        qlist.append({"qid": qid, "text": qtext, "options": opts})
    # save to dispatcher data
    dp.current_state(user=msg.from_user.id, chat=msg.chat.id)  # ensure state manager present
    state = dp.current_state(chat=msg.chat.id, user=msg.from_user.id)
    await state.update_data(current_quiz=quiz_id, questions=qlist, pointer=0, correct=0, total=len(qlist))
    first = qlist[0]
    kb = make_options_kb(first["qid"], first["options"])
    await msg.reply(f"Starting quiz: *{quiz[0]}*\nQuestion 1/{len(qlist)}:\n{first['text']}", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

### Callback handler for answers
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("ans|"))
async def process_answer(cb: types.CallbackQuery):
    await cb.answer()  # removes loading on client
    parts = cb.data.split("|")
    if len(parts) != 3:
        return
    qid = int(parts[1]); chosen = int(parts[2])
    state = dp.current_state(chat=cb.message.chat.id, user=cb.from_user.id)
    data = await state.get_data()
    if not data:
        await cb.message.reply("Session expired or not started. Use /take <quiz_id> to start.")
        return
    questions = data.get("questions", [])
    pointer = data.get("pointer", 0)
    correct = data.get("correct", 0)
    total = data.get("total", len(questions))
    # fetch correct answer for this qid
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT options, answer_index FROM questions WHERE id=?", (qid,))
        row = await cur.fetchone()
    if not row:
        await cb.message.reply("Question not found in DB.")
        return
    import json
    opts = json.loads(row[0]); ans_index = int(row[1])
    is_correct = (chosen == ans_index)
    if is_correct:
        correct += 1
    # increment pointer. NOTE: pointer might not correspond to qid order if admin added shuffled.
    pointer += 1
    await state.update_data(pointer=pointer, correct=correct)
    # respond to user about this question
    reply_text = ("✅ Correct!" if is_correct else f"❌ Wrong. Correct answer: *{opts[ans_index]}*")
    await cb.message.reply(reply_text, parse_mode=ParseMode.MARKDOWN)
    # next question or finish
    if pointer >= total:
        # save attempt
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO attempts (user_id, username, quiz_id, score, total) VALUES (?, ?, ?, ?, ?)",
                             (cb.from_user.id, cb.from_user.username or "", data["current_quiz"], correct, total))
            await db.commit()
        await cb.message.reply(f"Quiz finished! Your score: *{correct}/{total}*", parse_mode=ParseMode.MARKDOWN)
        # clear state
        await state.reset_data()
    else:
        next_q = questions[pointer]
        kb = make_options_kb(next_q["qid"], next_q["options"])
        await cb.message.reply(f"Question {pointer+1}/{total}:\n{next_q['text']}", reply_markup=kb)

### --- Score commands ---
@dp.message_handler(commands=["myscore"])
async def cmd_myscore(msg: types.Message):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT quiz_id, score, total, timestamp FROM attempts WHERE user_id=? ORDER BY timestamp DESC LIMIT 10", (msg.from_user.id,))
        rows = await cur.fetchall()
    if not rows:
        await msg.reply("You have no attempts yet.")
        return
    text = "Your recent attempts:\n" + "\n".join([f"Quiz {r[0]} — {r[1]}/{r[2]} on {r[3]}" for r in rows])
    await msg.reply(text)

@dp.message_handler(commands=["leaderboard"])
async def cmd_leaderboard(msg: types.Message):
    # global top by average score (simple)
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
        await msg.reply("No leaderboard yet.")
        return
    text = "Top performers:\n"
    i=1
    for r in rows:
        username = r[0] or "Unknown"
        pct = round(r[1]*100,2)
        attempts = r[2]
        text += f"{i}. {username} — {pct}% ({attempts} attempts)\n"
        i+=1
    await msg.reply(text)

### --- Startup ---
async def on_startup(dp):
    await init_db()
    logger.info("DB initialized.")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
