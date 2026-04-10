import asyncio
from flask import Flask
import threading
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, CallbackContext, ConversationHandler
)

# === LOGGING ===
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# === WEB SERVER ===
app_web = Flask(__name__)
@app_web.route('/')
def home(): return "Supreme Exchange Bot is Live"
def run_web(): app_web.run(host='0.0.0.0', port=8080)
threading.Thread(target=run_web, daemon=True).start()

# === CONFIG ===
BOT_TOKEN = "8229373861:AAGNNK9C_UF6rwbePcNDktz2VYJnqS1rua8"
ADMIN_ID = 8615263183

# === DB SETUP ===
conn = sqlite3.connect("exchange.db", check_same_thread=False)
cur = conn.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, bank_account TEXT, ifsc TEXT, name TEXT)')
cur.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, coin TEXT, amount REAL, inr_value REAL, screenshot_id TEXT, status TEXT, bank_info TEXT)')
cur.execute('CREATE TABLE IF NOT EXISTS coins (symbol TEXT PRIMARY KEY, price REAL, address TEXT)')
cur.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
conn.commit()

# === STATES ===
AMOUNT, COIN_SELECT, CONFIRM_SENT, SCREENSHOT, BANK_CHOICE, BANK_ACC, BANK_IFSC, BANK_NAME = range(8)

# === HELPERS ===
def maintenance_mode():
    cur.execute("SELECT value FROM settings WHERE key='maintenance'")
    row = cur.fetchone()
    return row and row[0] == "on"

def get_global_price():
    cur.execute("SELECT value FROM settings WHERE key='global_price'")
    row = cur.fetchone()
    return float(row[0]) if row else None

# === USER FLOW ===
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("👋 Welcome to Supreme Exchange!\n\nTo sell your crypto, use /sell\nTo check your transactions, use /history")

async def sell(update: Update, context: CallbackContext):
    if maintenance_mode():
        await update.message.reply_text("⚠️ The bot is under maintenance. Please try later.")
        return ConversationHandler.END
    await update.message.reply_text("💰 Enter the amount you want to sell:")
    return AMOUNT

async def handle_amount(update: Update, context: CallbackContext):
    try:
        amount = float(update.message.text)
        context.user_data['amount'] = amount
        cur.execute("SELECT symbol FROM coins")
        coins = cur.fetchall()
        if not coins:
            await update.message.reply_text("⚠️ No coins available.")
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton(c[0], callback_data=c[0])] for c in coins]
        await update.message.reply_text("📌 Select the coin:", reply_markup=InlineKeyboardMarkup(keyboard))
        return COIN_SELECT
    except:
        await update.message.reply_text("❌ Invalid amount, try again:")
        return AMOUNT

async def select_coin(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    coin = query.data
    context.user_data['coin'] = coin
    cur.execute("SELECT price, address FROM coins WHERE symbol=?", (coin,))
    price, address = cur.fetchone()
    g_price = get_global_price()
    if g_price: price = g_price

    val = round(context.user_data['amount'] * price, 2)
    context.user_data['inr_value'] = val
    context.user_data['address'] = address

    msg = (f"💵 *Order Summary*\nSelling: {context.user_data['amount']} {coin}\nReceive: ₹{val}\n\n"
           f"📥 *Address (Tap to copy):*\n<code>{address}</code>\n\n"
           "✅ Please send the exact amount to the address.\n\n"
           "⚠️ Note: Ensure that the amount shown after fee deduction is sent.\n\n"
           "After sending click the button below")
    
    keyboard = [[InlineKeyboardButton(f"✅ I have sent the {coin}", callback_data="done")]]
    await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return CONFIRM_SENT

async def ask_screenshot(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✨ Please send the **Payment Screenshot** as proof.\n\n🤖 Use the image for reference!!")
    return SCREENSHOT

async def handle_screenshot(update: Update, context: CallbackContext):
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a screenshot photo.")
        return SCREENSHOT
    context.user_data['screenshot_id'] = update.message.photo[-1].file_id
    user_id = update.message.from_user.id
    cur.execute("SELECT bank_account, ifsc, name FROM users WHERE user_id=?", (user_id,))
    bank = cur.fetchone()
    if bank:
        last_4 = bank[0][-4:]
        keyboard = [[InlineKeyboardButton(f"🏦 Use Saved Bank (****{last_4})", callback_data="old")],
                    [InlineKeyboardButton("➕ Add New Bank", callback_data="new")]]
        await update.message.reply_text("Bank account choice:", reply_markup=InlineKeyboardMarkup(keyboard))
        return BANK_CHOICE
    await update.message.reply_text("🏦 Enter Bank Account Number:")
    return BANK_ACC

async def bank_choice_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    if query.data == "old":
        cur.execute("SELECT bank_account, ifsc, name FROM users WHERE user_id=?", (query.from_user.id,))
        b = cur.fetchone()
        context.user_data['final_bank'] = f"Bank: {b[0]}\nIFSC: {b[1]}\nName: {b[2]}"
        return await save_transaction(query, context)
    await query.message.reply_text("🏦 Enter NEW Bank Account Number:")
    return BANK_ACC

async def bank_account(update: Update, context: CallbackContext):
    context.user_data['t_acc'] = update.message.text.strip()
    await update.message.reply_text("Enter IFSC:")
    return BANK_IFSC

async def bank_ifsc(update: Update, context: CallbackContext):
    context.user_data['t_ifsc'] = update.message.text.strip()
    await update.message.reply_text("Enter Name:")
    return BANK_NAME

async def bank_name(update: Update, context: CallbackContext):
    name = update.message.text.strip()
    acc, ifsc = context.user_data['t_acc'], context.user_data['t_ifsc']
    cur.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?)", (update.message.from_user.id, acc, ifsc, name))
    conn.commit()
    context.user_data['final_bank'] = f"Bank: {acc}\nIFSC: {ifsc}\nName: {name}"
    return await save_transaction(update, context)

async def save_transaction(u_or_q, context):
    user = u_or_q.from_user
    data = context.user_data
    cur.execute("INSERT INTO transactions (user_id, coin, amount, inr_value, screenshot_id, status, bank_info) VALUES (?,?,?,?,?,?,?)",
                (user.id, data['coin'], data['amount'], data['inr_value'], data['screenshot_id'], "pending", data['final_bank']))
    conn.commit()
    tid = cur.lastrowid
    admin_txt = f"🔔 Order #{tid}\nUser: {user.id}\n{data['amount']} {data['coin']} (₹{data['inr_value']})\n\n🏦 {data['final_bank']}"
    await context.bot.send_photo(ADMIN_ID, data['screenshot_id'], caption=admin_txt,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"ap_{tid}"), InlineKeyboardButton("❌ Reject", callback_data=f"rj_{tid}")]]))
    msg = "✅ Submitted! Admin verification pending."
    if hasattr(u_or_q, 'edit_message_text'): await u_or_q.edit_message_text(msg)
    else: await u_or_q.reply_text(msg)
    return ConversationHandler.END

# === ADMIN COMMANDS ===
async def addcoin(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    try:
        s, p, a = context.args[0].upper(), float(context.args[1]), context.args[2]
        cur.execute("INSERT OR REPLACE INTO coins VALUES (?,?,?)", (s, p, a))
        conn.commit()
        await update.message.reply_text(f"✅ {s} Added.")
    except: await update.message.reply_text("/addcoin <symbol> <price> <address>")

async def removecoin(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    cur.execute("DELETE FROM coins WHERE symbol=?", (context.args[0].upper(),))
    conn.commit()
    await update.message.reply_text("Done.")

async def setprice(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    cur.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("global_price", context.args[0]))
    conn.commit()
    await update.message.reply_text(f"Global Price: {context.args[0]}")

async def maintenance(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    cur.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("maintenance", context.args[0].lower()))
    conn.commit()
    await update.message.reply_text(f"Maintenance: {context.args[0]}")

async def transactions_list(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    cur.execute("SELECT id, user_id, amount, coin, status FROM transactions WHERE status='pending'")
    rows = cur.fetchall()
    if not rows: await update.message.reply_text("No pending orders.")
    else: await update.message.reply_text("\n".join([f"#{r[0]} User:{r[1]} - {r[2]} {r[3]}" for r in rows]))

async def history(update, context):
    cur.execute("SELECT id, coin, amount, status FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 5", (update.message.from_user.id,))
    rows = cur.fetchall()
    if not rows: await update.message.reply_text("No history.")
    else: await update.message.reply_text("Last 5 Orders:\n" + "\n".join([f"#{r[0]} {r[2]} {r[1]} - {r[3]}" for r in rows]))

async def button_handler(update, context):
    q = update.callback_query
    await q.answer()
    if "_" not in q.data: return
    act, tid = q.data.split("_")
    st = "approved" if act == "ap" else "rejected"
    cur.execute(f"UPDATE transactions SET status='{st}' WHERE id=?", (tid,))
    conn.commit()
    cur.execute("SELECT user_id FROM transactions WHERE id=?", (tid,))
    uid = cur.fetchone()[0]
    await q.edit_message_caption(f"Order #{tid} {st.upper()}.")
    await context.bot.send_message(uid, f"Your order #{tid} has been {st}.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("sell", sell)],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
            COIN_SELECT: [CallbackQueryHandler(select_coin)],
            CONFIRM_SENT: [CallbackQueryHandler(ask_screenshot, pattern="^done$")],
            SCREENSHOT: [MessageHandler(filters.PHOTO, handle_screenshot)],
            BANK_CHOICE: [CallbackQueryHandler(bank_choice_handler)],
            BANK_ACC: [MessageHandler(filters.TEXT & ~filters.COMMAND, bank_account)],
            BANK_IFSC: [MessageHandler(filters.TEXT & ~filters.COMMAND, bank_ifsc)],
            BANK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bank_name)],
        }, fallbacks=[]
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("removecoin", removecoin))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("maintenance", maintenance))
    app.add_handler(CommandHandler("transactions", transactions_list))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
        
