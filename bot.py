import json
import logging
import os
import sqlite3
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DATA_URL = "https://raw.githubusercontent.com/jyunfan/IVSchedulingStatusTracker/refs/heads/main/data/current.json"
DB_PATH = Path(os.environ.get("DB_PATH", "bot.db"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "3600"))

CATEGORIES = {
    "immediate_relative": "Immediate Relative",
    "family": "Family-Sponsored Preference",
    "employment": "Employment-Based Preference",
}


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """CREATE TABLE IF NOT EXISTS subscriptions (
            chat_id INTEGER,
            embassy TEXT,
            category TEXT,
            PRIMARY KEY (chat_id, embassy, category)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS last_seen (
            embassy TEXT,
            category TEXT,
            scheduling_date TEXT,
            source_last_updated TEXT,
            PRIMARY KEY (embassy, category)
        )"""
    )
    db.commit()
    return db


async def fetch_current_data() -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(DATA_URL, timeout=30)
        resp.raise_for_status()
        return resp.json()


def get_embassy_list(data: dict) -> list[str]:
    return sorted(e["embassy"] for e in data["data"])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to the IV Scheduling Status Bot!\n\n"
        "I'll notify you when the NVC updates scheduling dates "
        "for embassies and visa categories you're tracking.\n\n"
        "Commands:\n"
        "/subscribe - Subscribe to an embassy + category\n"
        "/unsubscribe - Remove a subscription\n"
        "/status - Check current scheduling date\n"
        "/mysubs - List your subscriptions"
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        data = await fetch_current_data()
    except Exception:
        await update.message.reply_text("Failed to fetch embassy list. Try again later.")
        return
    embassies = get_embassy_list(data)
    page = 0
    context.user_data["embassies"] = embassies
    await _send_embassy_page(update.message, embassies, page)


async def _send_embassy_page(message, embassies: list[str], page: int) -> None:
    page_size = 20
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_embassies = embassies[start_idx:end_idx]
    total_pages = (len(embassies) + page_size - 1) // page_size

    buttons = [
        [InlineKeyboardButton(e, callback_data=f"sub_embassy:{e}")]
        for e in page_embassies
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("< Prev", callback_data=f"sub_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next >", callback_data=f"sub_page:{page + 1}"))
    if nav:
        buttons.append(nav)

    await message.reply_text(
        f"Select an embassy (page {page + 1}/{total_pages}):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def sub_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    embassies = context.user_data.get("embassies", [])
    if not embassies:
        await query.edit_message_text("Session expired. Use /subscribe again.")
        return
    page_size = 20
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_embassies = embassies[start_idx:end_idx]
    total_pages = (len(embassies) + page_size - 1) // page_size

    buttons = [
        [InlineKeyboardButton(e, callback_data=f"sub_embassy:{e}")]
        for e in page_embassies
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("< Prev", callback_data=f"sub_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next >", callback_data=f"sub_page:{page + 1}"))
    if nav:
        buttons.append(nav)

    await query.edit_message_text(
        f"Select an embassy (page {page + 1}/{total_pages}):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def sub_embassy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    embassy = query.data.split(":", 1)[1]
    context.user_data["selected_embassy"] = embassy

    buttons = [
        [InlineKeyboardButton(label, callback_data=f"sub_cat:{key}")]
        for key, label in CATEGORIES.items()
    ]
    await query.edit_message_text(
        f"Embassy: {embassy}\nSelect a visa category:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def sub_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":")[1]
    embassy = context.user_data.get("selected_embassy")
    if not embassy:
        await query.edit_message_text("Session expired. Use /subscribe again.")
        return

    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO subscriptions (chat_id, embassy, category) VALUES (?, ?, ?)",
            (query.message.chat_id, embassy, category),
        )
        db.commit()
    finally:
        db.close()

    await query.edit_message_text(
        f"Subscribed to {embassy} - {CATEGORIES[category]}.\n"
        "You'll be notified when the scheduling date changes.\n\n"
        "Use /subscribe to add more, or /status to check current dates."
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT embassy, category FROM subscriptions WHERE chat_id = ?",
            (update.message.chat_id,),
        ).fetchall()
    finally:
        db.close()

    if not rows:
        await update.message.reply_text("You have no subscriptions.")
        return

    buttons = [
        [InlineKeyboardButton(
            f"{embassy} - {CATEGORIES[cat]}",
            callback_data=f"unsub:{embassy}:{cat}",
        )]
        for embassy, cat in rows
    ]
    await update.message.reply_text(
        "Select a subscription to remove:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def unsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    embassy, category = parts[1], parts[2]

    db = get_db()
    try:
        db.execute(
            "DELETE FROM subscriptions WHERE chat_id = ? AND embassy = ? AND category = ?",
            (query.message.chat_id, embassy, category),
        )
        db.commit()
    finally:
        db.close()

    await query.edit_message_text(f"Unsubscribed from {embassy} - {CATEGORIES[category]}.")


async def mysubs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT embassy, category FROM subscriptions WHERE chat_id = ? ORDER BY embassy, category",
            (update.message.chat_id,),
        ).fetchall()
    finally:
        db.close()

    if not rows:
        await update.message.reply_text("You have no subscriptions. Use /subscribe to add one.")
        return

    lines = [f"- {embassy} — {CATEGORIES[cat]}" for embassy, cat in rows]
    await update.message.reply_text("Your subscriptions:\n" + "\n".join(lines))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT embassy, category FROM subscriptions WHERE chat_id = ? ORDER BY embassy, category",
            (update.message.chat_id,),
        ).fetchall()
    finally:
        db.close()

    if not rows:
        await update.message.reply_text("You have no subscriptions. Use /subscribe to add one.")
        return

    try:
        data = await fetch_current_data()
    except Exception:
        await update.message.reply_text("Failed to fetch current data. Try again later.")
        return

    lookup = {e["embassy"]: e for e in data["data"]}
    lines = []
    for embassy, cat in rows:
        entry = lookup.get(embassy)
        if entry:
            date = entry.get(cat, "N/A")
            lines.append(f"- {embassy} | {CATEGORIES[cat]}: {date}")
        else:
            lines.append(f"- {embassy} | {CATEGORIES[cat]}: Embassy not found")

    header = f"Source last updated: {data.get('source_last_updated', 'Unknown')}\n\n"
    await update.message.reply_text(header + "\n".join(lines))


async def check_updates(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Checking for scheduling updates...")
    try:
        data = await fetch_current_data()
    except Exception:
        logger.exception("Failed to fetch data during update check")
        return

    source_updated = data.get("source_last_updated", "")
    lookup = {e["embassy"]: e for e in data["data"]}

    db = get_db()
    try:
        subs = db.execute("SELECT DISTINCT embassy, category FROM subscriptions").fetchall()

        for embassy, category in subs:
            entry = lookup.get(embassy)
            if not entry:
                continue
            new_date = entry.get(category, "")
            if not new_date:
                continue

            row = db.execute(
                "SELECT scheduling_date, source_last_updated FROM last_seen WHERE embassy = ? AND category = ?",
                (embassy, category),
            ).fetchone()

            old_date = row[0] if row else None
            old_source = row[1] if row else None

            if old_date != new_date or old_source != source_updated:
                db.execute(
                    "INSERT OR REPLACE INTO last_seen (embassy, category, scheduling_date, source_last_updated) VALUES (?, ?, ?, ?)",
                    (embassy, category, new_date, source_updated),
                )
                db.commit()

                if old_date is not None and old_date != new_date:
                    chat_ids = db.execute(
                        "SELECT chat_id FROM subscriptions WHERE embassy = ? AND category = ?",
                        (embassy, category),
                    ).fetchall()

                    for (chat_id,) in chat_ids:
                        msg = (
                            f"Scheduling update for {embassy} - {CATEGORIES[category]}:\n\n"
                            f"Previous: {old_date}\n"
                            f"New: {new_date}\n\n"
                            f"Source updated: {source_updated}"
                        )
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=msg)
                        except Exception:
                            logger.exception(f"Failed to notify chat {chat_id}")
    finally:
        db.close()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable")

    get_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("mysubs", mysubs))
    app.add_handler(CommandHandler("status", status))

    app.add_handler(CallbackQueryHandler(sub_page_callback, pattern=r"^sub_page:"))
    app.add_handler(CallbackQueryHandler(sub_embassy_callback, pattern=r"^sub_embassy:"))
    app.add_handler(CallbackQueryHandler(sub_cat_callback, pattern=r"^sub_cat:"))
    app.add_handler(CallbackQueryHandler(unsub_callback, pattern=r"^unsub:"))

    app.job_queue.run_repeating(check_updates, interval=CHECK_INTERVAL, first=10)

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
