import os
import sqlite3
import random
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")


# Database setup
conn = sqlite3.connect("archives.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS archives (
    code TEXT PRIMARY KEY,
    file_ids TEXT
)
""")
conn.commit()

user_states = {}
pack_storage = {}

def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Create Archive", callback_data="create")],
        [InlineKeyboardButton("Retrieve Archive", callback_data="retrieve")]
    ]
    await update.message.reply_text(
        "Choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "create":
        keyboard = [
            [InlineKeyboardButton("Single File", callback_data="single")],
            [InlineKeyboardButton("Pack", callback_data="pack")]
        ]
        await query.message.reply_text(
            "Select archive type:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "single":
        user_states[user_id] = "single"
        await query.message.reply_text("Send the file to archive.")

    elif query.data == "pack":
        user_states[user_id] = "pack"
        pack_storage[user_id] = []
        keyboard = [
            [InlineKeyboardButton("Done", callback_data="done_pack")]
        ]
        await query.message.reply_text(
            "Send all files. Click Done when finished.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "done_pack":
        files = pack_storage.get(user_id, [])
        if not files:
            await query.message.reply_text("No files received.")
            return

        code = generate_code()
        cursor.execute("INSERT INTO archives VALUES (?, ?)", (code, ",".join(files)))
        conn.commit()
        pack_storage[user_id] = []
        user_states[user_id] = None

        await query.message.reply_text(f"Pack archived.\nCode: {code}")

    elif query.data == "retrieve":
        user_states[user_id] = "retrieve"
        await query.message.reply_text("Send archive code.")

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    state = user_states.get(user_id)

    if state == "single":
        file_id = None

        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.video:
            file_id = update.message.video.file_id
        elif update.message.audio:
            file_id = update.message.audio.file_id

        if file_id:
            code = generate_code()
            cursor.execute("INSERT INTO archives VALUES (?, ?)", (code, file_id))
            conn.commit()
            user_states[user_id] = None
            await update.message.reply_text(f"Archived.\nCode: {code}")

    elif state == "pack":
        file_id = None

        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.video:
            file_id = update.message.video.file_id
        elif update.message.audio:
            file_id = update.message.audio.file_id

        if file_id:
            pack_storage[user_id].append(file_id)

    elif state == "retrieve":
        code = update.message.text.strip()
        cursor.execute("SELECT file_ids FROM archives WHERE code=?", (code,))
        result = cursor.fetchone()

        if result:
            file_ids = result[0].split(",")
            for fid in file_ids:
                await context.bot.send_document(chat_id=user_id, document=fid)
        else:
            await update.message.reply_text("Invalid code.")

        user_states[user_id] = None

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, file_handler))

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
