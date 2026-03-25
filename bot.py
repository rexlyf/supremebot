import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, CallbackContext, ConversationHandler
)

# === CONFIG ===
BOT_TOKEN = "8229373861:AAGNNK9C_UF6rwbePcNDktz2VYJnqS1rua8"
ADMIN_ID = 619893872

# === DB Setup ===
conn = sqlite3.connect("exchange.db", check_same_thread=False)
cur = conn.cursor()

# Users table
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    bank_account TEXT,
    ifsc TEXT,
    name TEXT
)''')

# Transactions table
cur.execute('''CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    coin TEXT,
    amount REAL,
    inr_value REAL,
    tx_hash TEXT UNIQUE,
    status TEXT
)''')

# Coins table
cur.execute('''CREATE TABLE IF NOT EXISTS coins (
    symbol TEXT PRIMARY KEY,
    price REAL,
    address TEXT
)''')

# Settings table
cur.execute('''CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)''')
conn.commit()

# === States ===
AMOUNT, COIN_SELECT, HASH, BANK_ACC, BANK_IFSC, BANK_NAME = range(6)

# === Helper functions ===
def maintenance_mode():
    cur.execute("SELECT value FROM settings WHERE key='maintenance'")
    row = cur.fetchone()
    return row and row[0] == "on"

def get_global_price():
    cur.execute("SELECT value FROM settings WHERE key='global_price'")
    row = cur.fetchone()
    return float(row[0]) if row else None

# === User Flow ===
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "👋 Welcome to Supreme Exchange!\n\n"
        "To sell your crypto, use /sell"
    )

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

        # Fetch available coins
        cur.execute("SELECT symbol FROM coins")
        coins = cur.fetchall()
        if not coins:
            await update.message.reply_text("⚠️ No coins available. Please try later.")
            return ConversationHandler.END

        keyboard = [[InlineKeyboardButton(c[0], callback_data=c[0])] for c in coins]
        await update.message.reply_text("📌 Select the coin you want to sell:", 
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        return COIN_SELECT
    except:
        await update.message.reply_text("❌ Invalid amount, try again:")
        return AMOUNT

async def select_coin(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    coin = query.data
    context.user_data['coin'] = coin

    # Get price and address
    cur.execute("SELECT price, address FROM coins WHERE symbol=?", (coin,))
    row = cur.fetchone()
    if not row:
        await query.message.reply_text("⚠️ Coin not found. Please try /sell again.")
        return ConversationHandler.END

    price, address = row
    global_price = get_global_price()
    if global_price:
        price = global_price  # override with global price if set

    context.user_data['address'] = address
    inr_value = round(context.user_data['amount'] * price, 2)
    context.user_data['inr_value'] = inr_value

    msg = (
        f"💵 You are selling {context.user_data['amount']} {coin}\n"
        f"Rate: {price} INR\n"
        f"You will receive: {inr_value} INR\n\n"
        f"📥 Send your {coin} to this address:\n{address}\n\n"
        "Tap the button below to copy the address:\n\n"
        "After sending Enter your Transaction hash ID and sumbit"
    )

    keyboard = [[InlineKeyboardButton("📋 Copy Address", switch_inline_query_current_chat=address)]]

    await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    return HASH

async def handle_hash(update: Update, context: CallbackContext):
    tx_hash = update.message.text.strip()
    user_id = update.message.from_user.id
    context.user_data['tx_hash'] = tx_hash

    # Check if user has bank details
    cur.execute("SELECT bank_account, ifsc, name FROM users WHERE user_id=?", (user_id,))
    bank = cur.fetchone()
    if bank:
        context.user_data['bank'] = bank
        return await save_transaction(update, context)
    else:
        await update.message.reply_text("🏦 Enter your bank account number:")
        return BANK_ACC

async def bank_account(update: Update, context: CallbackContext):
    context.user_data['bank_account'] = update.message.text.strip()
    await update.message.reply_text("Enter your IFSC code:")
    return BANK_IFSC

async def bank_ifsc(update: Update, context: CallbackContext):
    context.user_data['ifsc'] = update.message.text.strip()
    await update.message.reply_text("Enter account holder name:")
    return BANK_NAME

async def bank_name(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    name = update.message.text.strip()

    cur.execute("INSERT OR REPLACE INTO users (user_id, bank_account, ifsc, name) VALUES (?,?,?,?)",
                (user_id, context.user_data['bank_account'], context.user_data['ifsc'], name))
    conn.commit()
    context.user_data['bank'] = (context.user_data['bank_account'], context.user_data['ifsc'], name)

    return await save_transaction(update, context)

async def save_transaction(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    tx_hash = update.message.text.strip()

    # Get stored transaction data
    coin = context.user_data.get("coin")
    amount = context.user_data.get("amount")
    inr_value = context.user_data.get("inr_value")

    # Check for duplicate hash
    cur.execute("SELECT id FROM transactions WHERE tx_hash=?", (tx_hash,))
    if cur.fetchone():
        await update.message.reply_text(
            "⚠️ This transaction hash already exists!\n"
            "Please enter a new valid one."
        )
        return HASH

    try:
        cur.execute(
            "INSERT INTO transactions (user_id, coin, amount, inr_value, tx_hash, status) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, coin, amount, inr_value, tx_hash, "pending"),
        )
        conn.commit()
    except Exception as e:
        await update.message.reply_text("❌ Error saving transaction. Please try again.")
        print(f"DB Error: {e}")
        return HASH

    tx_id = cur.lastrowid

    # 🔹 Fetch bank details for admin
    cur.execute("SELECT bank_account, ifsc, name FROM users WHERE user_id=?", (user_id,))
    bank = cur.fetchone()
    bank_info = f"🏦 Bank: {bank[0]}\nIFSC: {bank[1]}\nName: {bank[2]}" if bank else "❌ No bank details found"

    keyboard = [
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{tx_id}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"reject_{tx_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        ADMIN_ID,
        f"🔔 New Transaction Request\n\n"
        f"👤 User: {user_id}\n"
        f"💰 Coin: {coin}\n"
        f"🔢 Amount: {amount}\n"
        f"₹ Value: {inr_value}\n"
        f"🔗 Hash: {tx_hash}\n\n"
        f"{bank_info}",
        reply_markup=reply_markup
    )

    await update.message.reply_text("✅ Your transaction has been submitted and is pending admin approval.")
    return ConversationHandler.END

# === Admin Commands ===
async def addcoin(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    try:
        symbol, price, address = context.args[0], float(context.args[1]), context.args[2]
        cur.execute("INSERT OR REPLACE INTO coins (symbol, price, address) VALUES (?,?,?)",
                    (symbol.upper(), price, address))
        conn.commit()
        await update.message.reply_text(f"✅ {symbol.upper()} added with rate {price} INR and address {address}")
    except:
        await update.message.reply_text("❌ Usage: /addcoin <symbol> <price> <address>")

async def removecoin(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    try:
        symbol = context.args[0].upper()
        cur.execute("DELETE FROM coins WHERE symbol=?", (symbol,))
        conn.commit()
        await update.message.reply_text(f"❌ Coin {symbol} removed successfully.")
    except:
        await update.message.reply_text("❌ Usage: /removecoin <symbol>")

async def setprice(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    try:
        price = float(context.args[0])
        cur.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("global_price", str(price)))
        conn.commit()
        await update.message.reply_text(f"✅ Global price set to {price} INR for all coins.")
    except:
        await update.message.reply_text("❌ Usage: /setprice <price>")

async def maintenance(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    try:
        state = context.args[0].lower()
        if state not in ["on", "off"]:
            raise Exception
        cur.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("maintenance", state))
        conn.commit()
        await update.message.reply_text(f"🔧 Maintenance mode {state.upper()}")
    except:
        await update.message.reply_text("❌ Usage: /maintenance on|off")

# Admin approve/reject buttons
async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data: return
    action, tx_id = data.split("_", 1)
    tx_id = int(tx_id)

    if action == "approve":
        cur.execute("UPDATE transactions SET status='approved' WHERE id=?", (tx_id,))
        conn.commit()
        await query.edit_message_text(f"✅ Transaction {tx_id} approved.")

        cur.execute("SELECT user_id, tx_hash FROM transactions WHERE id=?", (tx_id,))
        uid, tx_hash = cur.fetchone()
        await context.bot.send_message(uid, f"✅ Your transaction {tx_hash} has been approved.")

    elif action == "reject":
        cur.execute("UPDATE transactions SET status='rejected' WHERE id=?", (tx_id,))
        conn.commit()
        await query.edit_message_text(f"❌ Transaction {tx_id} rejected.")

        cur.execute("SELECT user_id, tx_hash FROM transactions WHERE id=?", (tx_id,))
        uid, tx_hash = cur.fetchone()
        await context.bot.send_message(uid, f"❌ Your transaction {tx_hash} has been rejected.")

# Admin view pending transactions
async def transactions(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    cur.execute("SELECT id,user_id,coin,amount,inr_value,tx_hash FROM transactions WHERE status='pending'")
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No pending transactions.")
    else:
        text = "📋 Pending Transactions:\n"
        for r in rows:
            cur.execute("SELECT bank_account,ifsc,name FROM users WHERE user_id=?", (r[1],))
            bank = cur.fetchone()
            text += (f"ID:{r[0]} User:{r[1]} {r[3]} {r[2]} (~{r[4]} INR) Hash:{r[5]}\n"
                     f"🏦 Bank: {bank[0]}, IFSC: {bank[1]}, Name: {bank[2]}\n\n")
        await update.message.reply_text(text)

# User view history
async def history(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    cur.execute("SELECT coin,amount,inr_value,tx_hash,status FROM transactions WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No transactions found.")
    else:
        text = "📜 Your Transaction History:\n"
        for r in rows:
            text += f"{r[1]} {r[0]} (~{r[2]} INR) Hash:{r[3]} Status:{r[4]}\n"
        await update.message.reply_text(text)

# === Main ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("sell", sell)],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
            COIN_SELECT: [CallbackQueryHandler(select_coin)],
            HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hash)],
            BANK_ACC: [MessageHandler(filters.TEXT & ~filters.COMMAND, bank_account)],
            BANK_IFSC: [MessageHandler(filters.TEXT & ~filters.COMMAND, bank_ifsc)],
            BANK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bank_name)],
        },
        fallbacks=[]
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("removecoin", removecoin))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("maintenance", maintenance))
    app.add_handler(CommandHandler("transactions", transactions))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Supreme Exchange Bot Running")
    app.run_polling()

if __name__ == "__main__":
    main()
