import os
import sqlite3
import random
import string
import asyncio
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

OWNER_ID = 1630567215
TOKEN = os.getenv("BOT_TOKEN")

# ================= DATABASE =================

conn = sqlite3.connect("archives.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS archives (
    code TEXT PRIMARY KEY,
    owner_id INTEGER,
    file_ids TEXT,
    name TEXT,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS access_keys (
    key TEXT PRIMARY KEY,
    expires_at TEXT,
    used INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS authorized_users (
    user_id INTEGER PRIMARY KEY,
    expires_at TEXT
)
""")

conn.commit()

# ================= UTIL =================

def now():
    from datetime import datetime, UTC
    return datetime.now(UTC)

def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def generate_key(length=12):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ================= ACCESS CONTROL =================

def is_authorized(user_id):
    if user_id == OWNER_ID:
        return True

    cursor.execute(
        "SELECT expires_at FROM authorized_users WHERE user_id=?",
        (user_id,)
    )
    result = cursor.fetchone()

    if not result:
        return False

    expires_at = datetime.fromisoformat(result[0])
    if now() > expires_at:
        cursor.execute("DELETE FROM authorized_users WHERE user_id=?", (user_id,))
        conn.commit()
        return False

    return True

# ================= UI HELPERS =================

def main_menu_keyboard(user_id):
    buttons = [
        [InlineKeyboardButton("📦 Create Archive", callback_data="create")],
        [InlineKeyboardButton("📁 My Archives", callback_data="my_archives")],
        [InlineKeyboardButton("🔍 Search", callback_data="search")]
    ]

    if user_id == OWNER_ID:
        buttons.append(
            [InlineKeyboardButton("🔑 Generate Access Key", callback_data="gen_key")]
        )

    return InlineKeyboardMarkup(buttons)

def back_button():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅ Back", callback_data="main_menu")]]
    )

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await update.message.reply_text(
            "You are not allowed to use this bot.\n"
            "If you have a key from the owner, send it now."
        )
        context.user_data["awaiting_key"] = True
        return

    await update.message.reply_text(
        "Main Menu:",
        reply_markup=main_menu_keyboard(user_id)
    )

# ================= BUTTON HANDLER =================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_authorized(user_id):
        await query.answer("Access expired or not allowed.", show_alert=True)
        return

    data = query.data

    if data == "main_menu":
        context.user_data.clear()
        await query.message.reply_text(
            "Main Menu:",
            reply_markup=main_menu_keyboard(user_id)
        )

    elif data == "create":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Single File", callback_data="create_single")],
            [InlineKeyboardButton("Pack", callback_data="create_pack")],
            [InlineKeyboardButton("⬅ Back", callback_data="main_menu")]
        ])
        await query.message.reply_text("Select type:", reply_markup=keyboard)

    elif data == "create_single":
        context.user_data["mode"] = "single"
        await query.message.reply_text("Send the file.", reply_markup=back_button())

    elif data == "create_pack":
        context.user_data["mode"] = "pack"
        context.user_data["pack"] = []
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Done", callback_data="pack_done")],
            [InlineKeyboardButton("⬅ Back", callback_data="main_menu")]
        ])
        await query.message.reply_text(
            "Send files. Press Done when finished.",
            reply_markup=keyboard
        )

    elif data == "pack_done":
        files = context.user_data.get("pack", [])
        if not files:
            await query.message.reply_text("No files received.", reply_markup=back_button())
            return

        code = generate_code()
        context.user_data["pending_archive"] = {
            "code": code,
            "files": ",".join(files)
        }
        context.user_data["awaiting_name"] = True

        await query.message.reply_text(
            f"Code: {code}\nEnter name or type skip.",
            reply_markup=back_button()
        )

    elif data == "my_archives":
        await show_archives(query, context, page=0)

    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        await show_archives(query, context, page)

    elif data.startswith("open_"):
        code = data.split("_")[1]
        context.user_data["current_archive"] = code
        await show_archive_menu(query, context, code)

    elif data == "retrieve_archive":
        await retrieve_archive(query, context)

    elif data == "delete_archive":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Confirm Delete", callback_data="confirm_delete")],
            [InlineKeyboardButton("⬅ Back", callback_data="my_archives")]
        ])
        await query.message.reply_text("Are you sure?", reply_markup=keyboard)

    elif data == "confirm_delete":
        code = context.user_data.get("current_archive")
        cursor.execute(
            "DELETE FROM archives WHERE code=? AND owner_id=?",
            (code, user_id)
        )
        conn.commit()
        await query.message.reply_text("Deleted.", reply_markup=back_button())

    elif data == "rename_archive":
        context.user_data["rename_mode"] = True
        await query.message.reply_text("Enter new name.", reply_markup=back_button())

    elif data == "search":
        context.user_data["search_mode"] = True
        await query.message.reply_text("Enter keyword.", reply_markup=back_button())

    elif data == "gen_key" and user_id == OWNER_ID:
        key = generate_key()
        expires = now() + timedelta(hours=24)

        cursor.execute(
            "INSERT INTO access_keys VALUES (?, ?, 0)",
            (key, expires.isoformat())
        )
        conn.commit()

        await query.message.reply_text(
            f"Key: {key}\nValid 24 hours.",
            reply_markup=back_button()
        )

# ================= ARCHIVE LIST =================

async def show_archives(query, context, page):
    user_id = query.from_user.id
    per_page = 10
    offset = page * per_page

    cursor.execute(
        "SELECT code, name FROM archives WHERE owner_id=? LIMIT ? OFFSET ?",
        (user_id, per_page, offset)
    )
    results = cursor.fetchall()

    if not results:
        await query.message.reply_text("No archives.", reply_markup=back_button())
        return

    keyboard = []
    for code, name in results:
        display = name if name else code
        keyboard.append([
            InlineKeyboardButton(display, callback_data=f"open_{code}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"page_{page-1}"))
    if len(results) == per_page:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"page_{page+1}"))

    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="main_menu")])

    await query.message.reply_text(
        "Your Archives:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= ARCHIVE MENU =================

async def show_archive_menu(query, context, code):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Retrieve", callback_data="retrieve_archive")],
        [InlineKeyboardButton("✏ Rename", callback_data="rename_archive")],
        [InlineKeyboardButton("🗑 Delete", callback_data="delete_archive")],
        [InlineKeyboardButton("⬅ Back", callback_data="my_archives")]
    ])

    await query.message.reply_text(
        f"Archive: {code}",
        reply_markup=keyboard
    )

# ================= RETRIEVE =================

async def retrieve_archive(query, context):
    user_id = query.from_user.id
    code = context.user_data.get("current_archive")

    cursor.execute(
        "SELECT file_ids FROM archives WHERE code=? AND owner_id=?",
        (code, user_id)
    )
    result = cursor.fetchone()

    if not result:
        await query.message.reply_text("Not found.", reply_markup=back_button())
        return

    await query.message.reply_text("Retrieving...")

    file_ids = result[0].split(",")
    for fid in file_ids:
        await context.bot.send_document(chat_id=user_id, document=fid)
        await asyncio.sleep(0.4)

    await query.message.reply_text("Done.", reply_markup=back_button())

# ================= MESSAGE HANDLER =================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # KEY SUBMISSION
    if context.user_data.get("awaiting_key"):
        cursor.execute(
            "SELECT expires_at, used FROM access_keys WHERE key=?",
            (text,)
        )
        result = cursor.fetchone()

        if not result:
            await update.message.reply_text("Invalid key.")
            return

        expires_at = datetime.fromisoformat(result[0])
        used = result[1]

        if used or now() > expires_at:
            await update.message.reply_text("Key expired or used.")
            return

        user_expiry = now() + timedelta(hours=24)

        cursor.execute(
            "INSERT OR REPLACE INTO authorized_users VALUES (?, ?)",
            (user_id, user_expiry.isoformat())
        )
        cursor.execute(
            "UPDATE access_keys SET used=1 WHERE key=?",
            (text,)
        )
        conn.commit()

        context.user_data.clear()

        await update.message.reply_text(
            "Access granted.",
            reply_markup=main_menu_keyboard(user_id)
        )
        return

    if not is_authorized(user_id):
        return

    # SEARCH
    if context.user_data.get("search_mode"):
        keyword = text
        cursor.execute(
            "SELECT code, name FROM archives WHERE owner_id=? AND name LIKE ?",
            (user_id, f"%{keyword}%")
        )
        results = cursor.fetchall()

        if not results:
            await update.message.reply_text("No matches.", reply_markup=back_button())
            return

        keyboard = [
            [InlineKeyboardButton(name or code, callback_data=f"open_{code}")]
            for code, name in results
        ]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="main_menu")])

        await update.message.reply_text(
            "Search Results:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        context.user_data.pop("search_mode")
        return

    # RENAME
    if context.user_data.get("rename_mode"):
        new_name = text
        code = context.user_data.get("current_archive")

        cursor.execute(
            "UPDATE archives SET name=? WHERE code=? AND owner_id=?",
            (new_name, code, user_id)
        )
        conn.commit()

        context.user_data.pop("rename_mode")
        await update.message.reply_text("Renamed.", reply_markup=back_button())
        return

    # CREATE SINGLE
    mode = context.user_data.get("mode")

    if mode == "single":
        file_id = None
        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.video:
            file_id = update.message.video.file_id
        elif update.message.audio:
            file_id = update.message.audio.file_id

        if file_id:
            code = generate_code()
            context.user_data["pending_archive"] = {
                "code": code,
                "files": file_id
            }
            context.user_data["awaiting_name"] = True

            await update.message.reply_text(
                f"Code: {code}\nEnter name or type skip.",
                reply_markup=back_button()
            )

    # CREATE PACK
    elif mode == "pack":
        if update.message.document:
            context.user_data["pack"].append(update.message.document.file_id)
        elif update.message.video:
            context.user_data["pack"].append(update.message.video.file_id)
        elif update.message.audio:
            context.user_data["pack"].append(update.message.audio.file_id)

    # AWAITING NAME
    if context.user_data.get("awaiting_name"):
        name = text
        pending = context.user_data.get("pending_archive")

        if not pending:
            return

        if name.lower() == "skip":
            name = None

        cursor.execute(
            "INSERT INTO archives VALUES (?, ?, ?, ?, ?)",
            (
                pending["code"],
                user_id,
                pending["files"],
                name,
                now().isoformat()
            )
        )
        conn.commit()

        context.user_data.clear()

        await update.message.reply_text("Saved.", reply_markup=back_button())

# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, message_handler))

    app.run_polling()

if __name__ == "__main__":
    main()