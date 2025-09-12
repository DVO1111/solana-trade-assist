import os
from dotenv import load_dotenv
from typing import Tuple, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ===== ENV LOADING =====
local_env_path = os.path.join(os.path.dirname(__file__), ".env")
user_env_path = r"C:\Users\HP\.env"
if os.path.exists(local_env_path):
    load_dotenv(local_env_path)
    print(f"[ENV] Loaded from {local_env_path}")
elif os.path.exists(user_env_path):
    load_dotenv(user_env_path)
    print(f"[ENV] Loaded from {user_env_path}")
else:
    print("[ENV] No .env file found — relying on system environment variables.")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in environment")

# ===== CONVERSATION STATES =====
(
    CHOOSING,
    BUY_ASK_MINT,
    BUY_ASK_AMOUNT,
    BUY_CONFIRM,
    SELL_ASK_MINT,
    SELL_ASK_AMOUNT,
    SELL_CONFIRM,
) = range(7)

# ===== HELPERS =====
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Buy", callback_data="BUY"),
         InlineKeyboardButton("🔴 Sell", callback_data="SELL")],
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL")]
    ])

def confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data=f"{action}_CONFIRM"),
         InlineKeyboardButton("↩️ Back", callback_data="BACK"),
         InlineKeyboardButton("❌ Cancel", callback_data="CANCEL")]
    ])

def is_valid_mint(mint: str) -> bool:
    # Basic sanity checks for Solana mint addresses
    return len(mint.strip()) >= 32 and " " not in mint

def parse_amount(text: str) -> Optional[float]:
    try:
        val = float(text.strip())
        return val if val > 0 else None
    except Exception:
        return None

# ===== COMMAND HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_chat.send_message(
        "Welcome. Choose an action:",
        reply_markup=main_menu_keyboard()
    )
    return CHOOSING

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start — show main menu\n"
        "/cancel — cancel current action\n"
        "Use buttons to Buy or Sell."
    )

async def cancel_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Use /start to begin again.")
    return ConversationHandler.END

# ===== CALLBACK HANDLERS (BUTTONS) =====
async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "BUY":
        context.user_data["action"] = "BUY"
        await query.edit_message_text("Buy selected.\nSend the token mint address:")
        return BUY_ASK_MINT

    if query.data == "SELL":
        context.user_data["action"] = "SELL"
        await query.edit_message_text("Sell selected.\nSend the token mint address:")
        return SELL_ASK_MINT

    if query.data == "CANCEL":
        context.user_data.clear()
        await query.edit_message_text("Cancelled. Use /start to begin again.")
        return ConversationHandler.END

    if query.data == "BACK":
        # Return to main menu
        context.user_data.pop("action", None)
        context.user_data.pop("mint", None)
        context.user_data.pop("amount", None)
        await query.edit_message_text("Choose an action:", reply_markup=main_menu_keyboard())
        return CHOOSING

    # Confirms handled in dedicated handlers below
    await query.edit_message_text("Unknown option. Use /start.")
    return ConversationHandler.END

# ===== BUY FLOW =====
async def buy_receive_mint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mint = (update.message.text or "").strip()
    if not is_valid_mint(mint):
        await update.message.reply_text("That doesn't look like a valid mint. Please send a valid SPL mint address.")
        return BUY_ASK_MINT

    context.user_data["mint"] = mint
    await update.message.reply_text(
        f"Mint set: {mint}\nEnter buy amount in SOL (e.g., 0.05):"
    )
    return BUY_ASK_AMOUNT

async def buy_receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amt = parse_amount(update.message.text or "")
    if amt is None:
        await update.message.reply_text("Invalid amount. Enter a positive number (e.g., 0.05):")
        return BUY_ASK_AMOUNT

    context.user_data["amount"] = amt
    mint = context.user_data["mint"]
    await update.message.reply_text(
        f"Review your order:\n"
        f"Action: BUY\n"
        f"Mint: {mint}\n"
        f"Amount: {amt} SOL\n\n"
        f"Confirm?",
        reply_markup=confirm_keyboard("BUY")
    )
    return BUY_CONFIRM

async def buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "BUY_CONFIRM":
        mint = context.user_data.get("mint")
        amount = context.user_data.get("amount")
        # TODO: Wire this to your Jupiter swap execution
        ok, note = await perform_swap_stub(direction="BUY", mint=mint, amount_sol=amount)
        prefix = "✅ Success" if ok else "❌ Failed"
        await query.edit_message_text(f"{prefix}: {note}")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data in ("BACK", "CANCEL"):
        context.user_data.clear()
        await query.edit_message_text("Cancelled. Use /start to begin again.")
        return ConversationHandler.END

    await query.edit_message_text("Unknown selection. Use /start.")
    return ConversationHandler.END

# ===== SELL FLOW =====
async def sell_receive_mint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mint = (update.message.text or "").strip()
    if not is_valid_mint(mint):
        await update.message.reply_text("That doesn't look like a valid mint. Please send a valid SPL mint address.")
        return SELL_ASK_MINT

    context.user_data["mint"] = mint
    await update.message.reply_text(
        f"Mint set: {mint}\nEnter sell amount in token units (e.g., 100000):"
    )
    return SELL_ASK_AMOUNT

async def sell_receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amt = parse_amount(update.message.text or "")
    if amt is None:
        await update.message.reply_text("Invalid amount. Enter a positive number (e.g., 100000):")
        return SELL_ASK_AMOUNT

    context.user_data["amount"] = amt
    mint = context.user_data["mint"]
    await update.message.reply_text(
        f"Review your order:\n"
        f"Action: SELL\n"
        f"Mint: {mint}\n"
        f"Amount: {amt} tokens\n\n"
        f"Confirm?",
        reply_markup=confirm_keyboard("SELL")
    )
    return SELL_CONFIRM

async def sell_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "SELL_CONFIRM":
        mint = context.user_data.get("mint")
        amount = context.user_data.get("amount")
        # TODO: Wire this to your Jupiter swap execution
        ok, note = await perform_swap_stub(direction="SELL", mint=mint, amount_tokens=amount)
        prefix = "✅ Success" if ok else "❌ Failed"
        await query.edit_message_text(f"{prefix}: {note}")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data in ("BACK", "CANCEL"):
        context.user_data.clear()
        await query.edit_message_text("Cancelled. Use /start to begin again.")
        return ConversationHandler.END

    await query.edit_message_text("Unknown selection. Use /start.")
    return ConversationHandler.END

# ===== SWAP EXECUTION (STUB) =====
async def perform_swap_stub(direction: str, mint: str, amount_sol: float = None, amount_tokens: float = None) -> Tuple[bool, str]:
    # Placeholder. Here you will:
    # 1) Build a Jupiter route (quote)
    # 2) Build a transaction
    # 3) Sign with your PRIVATE_KEY
    # 4) Send and confirm
    if direction == "BUY" and amount_sol is not None:
        return True, f"Buy order queued: {amount_sol} SOL → {mint} (stub)."
    if direction == "SELL" and amount_tokens is not None:
        return True, f"Sell order queued: {amount_tokens} of {mint} (stub)."
    return False, "Invalid parameters for swap."

# ===== CONVERSATION WIRES =====
def build_application():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [
                CallbackQueryHandler(choose_action, pattern="^(BUY|SELL|CANCEL|BACK)$"),
            ],
            BUY_ASK_MINT: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_receive_mint),
                           CallbackQueryHandler(choose_action)],
            BUY_ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_receive_amount),
                             CallbackQueryHandler(choose_action)],
            BUY_CONFIRM: [CallbackQueryHandler(buy_confirm, pattern="^(BUY_CONFIRM|BACK|CANCEL)$")],
            SELL_ASK_MINT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_receive_mint),
                            CallbackQueryHandler(choose_action)],
            SELL_ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_receive_amount),
                              CallbackQueryHandler(choose_action)],
            SELL_CONFIRM: [CallbackQueryHandler(sell_confirm, pattern="^(SELL_CONFIRM|BACK|CANCEL)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_all)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", help_cmd))
    return app

async def on_startup(app):
    print("[BOT] Bot started and ready.")

def main():
    app = build_application()
    app.post_init = on_startup
    print("[BOT] Starting polling...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()