# --- Telegram Bot Script ---
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
import gspread
from google.oauth2.service_account import Credentials
import datetime
import logging
import json
from typing import List

# --- Load Configurations from External File ---
CONFIG_FILE = 'config.json'  # Path to the configuration file
with open(CONFIG_FILE, 'r') as config_file:
    config = json.load(config_file)

BOT_TOKEN = config['BOT_TOKEN']
SPREADSHEET_ID = config['SPREADSHEET_ID']
SHEET_NAME = config['SHEET_NAME']
ALLOWED_USER_IDS = config.get('ALLOWED_USER_IDS', []) # Get whitelist from config, default to empty list

# --- Divider Symbol (configurable via bot) ---
divider_symbol = '$'

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- Google Sheets Authentication ---
def authenticate_gspread():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]
    creds = Credentials.from_service_account_file(config['CREDENTIALS_FILE'], scopes=scopes)
    gc = gspread.authorize(creds)
    return gc

gc = authenticate_gspread()  # Authenticate once at startup
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

# --- Authorization Decorator ---
def authorized_user(func):
    async def wrapper(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USER_IDS:
            instruction_message = "To get access, ask bot administrator to add your User ID: " \
                                  f"`{user_id}` to the `ALLOWED_USER_IDS` array in `config.json` file."
            await update.message.reply_text(f"Unauthorized access. {instruction_message}", parse_mode=telegram.constants.ParseMode.MARKDOWN)
            logger.warning(f"Unauthorized user {user_id} tried to access {func.__name__}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# --- Function to write data to Google Sheets ---
def write_to_sheet(item, price, category=None):
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M:%S")
    next_row = len(list(filter(None, sheet.col_values(1)))) + 1  # Find next empty row
    row_values = [date, time, item, price, category]
    sheet.insert_row(row_values, next_row)

# --- Function to get spending summary ---
def get_spending_summary(period: str, category=None, top_amount=None) -> str:
    """
    Calculates the total spending for a given period (day, week, or month) from a Google Sheet,
    optionally filtered by category and showing top spendings.
    Returns: str: Spending summary message.
    """
    try:
        raw_data: List[List[str]] = sheet.get_all_values()
    except Exception as e:
        logger.error(f"Failed to fetch data from the sheet: {e}")
        return "Error fetching data."

    if not raw_data or len(raw_data) <= 1:  # Assume first row is header or sheet is empty
        return "No spending data available."

    rows = raw_data[1:] # Skip header row if exists, assuming header is in the first row
    today = datetime.date.today()
    period_total = 0.0
    spending_items = []

    for row in rows:
        if len(row) < 5: # Ensure row has enough columns
            continue
        try:
            date_str = row[0].strip()
            price_str = row[3].strip()
            item_name = row[2].strip()
            row_category = row[4].strip() if len(row) > 4 else None # Category from sheet
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            price = float(price_str)

            if category and category.lower() not in [cat.strip().lower() for cat in (row_category or "").split(',')]: #Check if row category contains requested category
                continue

            in_period = False
            if period == 'day' and date_obj == today:
                in_period = True
            elif period == 'week':
                start_of_week = today - datetime.timedelta(days=today.weekday())
                if start_of_week <= date_obj <= today:
                    in_period = True
            elif period == 'month' and date_obj.year == today.year and date_obj.month == today.month:
                in_period = True

            if in_period:
                period_total += price
                spending_items.append({'name': item_name, 'price': price, 'category': row_category, 'date': date_obj})

        except (ValueError, IndexError) as e:
            logger.warning(f"Skipping invalid row {row}: {e}")

    if not spending_items:
        return f"No spendings recorded for this {period}{f' in {category}' if category else ''}."

    spending_items.sort(key=lambda x: x['price'], reverse=True) # Sort by price

    if top_amount and top_amount < len(spending_items):
        spending_items = spending_items[:top_amount]

    summary_message = f"Spending for {period}{f' in {category}' if category else ''}:\n"
    for item in spending_items:
        days_ago = (today - item['date']).days
        date_display = f"{days_ago} day{'s' if days_ago != 1 else ''} ago" if period == 'day' else item['date'].strftime("%Y-%m-%d")
        summary_message += f"- {item['name']} ({item['category'] or 'N/A'}): {item['price']:.2f}{divider_symbol} ({date_display})\n"
    summary_message += f"\nTotal for {period}: {period_total:.2f}{divider_symbol}"

    return summary_message


# --- Category Management ---
@authorized_user
async def add_category(update, context):
    if not context.args:
        await update.message.reply_text("Please provide a category name to add. Example: /addcat Groceries")
        return
    category_name = " ".join(context.args)
    categories_col = sheet.col_values(5)
    if category_name.lower() in [cat.strip().lower() for cell in categories_col if cell for cat in cell.split(',')]: # Check if category already exists (case-insensitive)
        await update.message.reply_text(f"Category '{category_name}' already exists.")
        return

    categories_row = len(list(filter(None, sheet.col_values(5)))) + 1
    sheet.update_cell(categories_row, 5, category_name) # Add category to the end of category column
    await update.message.reply_text(f"Category '{category_name}' added.")

@authorized_user
async def remove_category(update, context):
    if not context.args:
        await update.message.reply_text("Please provide a category name to remove. Example: /removecat Groceries")
        return
    category_name = " ".join(context.args)
    categories_col = sheet.col_values(5)
    found = False
    for i, cell_value in enumerate(categories_col):
        if cell_value:
            categories_in_cell = [cat.strip() for cat in cell_value.split(',')]
            if category_name in categories_in_cell:
                categories_in_cell.remove(category_name)
                updated_cell_value = ", ".join(categories_in_cell)
                sheet.update_cell(i+1, 5, updated_cell_value) # Update the cell, i+1 because lists are 0-indexed and sheets are 1-indexed
                found = True
                break # Assuming each category name is unique across all cells
    if found:
        await update.message.reply_text(f"Category '{category_name}' removed.")
    else:
        await update.message.reply_text(f"Category '{category_name}' not found.")

@authorized_user
async def edit_category(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Please provide the old and new category names. Example: /editcat OldCategory NewCategory")
        return
    old_category_name = context.args[0]
    new_category_name = " ".join(context.args[1:])

    categories_col = sheet.col_values(5)
    found = False
    for i, cell_value in enumerate(categories_col):
        if cell_value:
            categories_in_cell = [cat.strip() for cat in cell_value.split(',')]
            if old_category_name in categories_in_cell:
                categories_in_cell = [new_category_name if cat == old_category_name else cat for cat in categories_in_cell] # Replace old with new
                updated_cell_value = ", ".join(categories_in_cell)
                sheet.update_cell(i+1, 5, updated_cell_value)
                found = True
                break
    if found:
        await update.message.reply_text(f"Category '{old_category_name}' updated to '{new_category_name}'.")
    else:
        await update.message.reply_text(f"Category '{old_category_name}' not found.")

# --- Command Handlers ---
@authorized_user
async def start(update, context):
    user = update.effective_user
    main_functions_description = """
    \n**Main Functions:**
    - 📝 **Track Expenses:** Send messages like `Item{divider}Price` (e.g., `Coffee$10`) to record expenses.
    - 📊 **Spending Reports:** Get summaries for today, this week, or this month.
    - 🗂️ **Category Management:**  Organize your expenses by categories, and manage them easily.
    - ⚙️ **Customizable Divider:** Set your preferred divider symbol (default is `$`).
    """
    commands_description = """
    \n**Commands:**
    /help - Show help message
    /day [category] - Get spending for today
    /week [category] - Get spending for this week
    /month [category] - Get spending for this month
    /setdivider [symbol] - Set divider symbol
    /addcat [category name] - Add category
    /removecat [category name] - Remove category
    /editcat [old category] [new category] - Edit category
    /categories - Show category buttons
    """
    await update.message.reply_markdown_v2(
        fr"Hi {user.mention_markdown_v2()}! I am your personal expense tracker bot\. \
I help you manage your spendings by recording them in a Google Sheet\.{main_functions_description}{commands_description} \
Use bot menu button to see commands for quick access\.",
    )


@authorized_user
async def set_divider_command(update, context):
    global divider_symbol
    if context.args:
        new_divider = context.args[0]
        if len(new_divider) == 1:
            divider_symbol = new_divider
            await update.message.reply_text(f'Divider symbol set to: {divider_symbol}')
        else:
            await update.message.reply_text('Divider symbol must be a single character.')
    else:
        await update.message.reply_text('Please provide a divider symbol. For example: /setdivider #')

@authorized_user
async def day_spending_command(update, context):
    category = " ".join(context.args) if context.args else None
    report = get_spending_summary('day', category=category)
    await update.message.reply_text(report)

@authorized_user
async def week_spending_command(update, context):
    category = " ".join(context.args) if context.args else None
    report = get_spending_summary('week', category=category)
    await update.message.reply_text(report)

@authorized_user
async def month_spending_command(update, context):
    category = " ".join(context.args) if context.args else None
    report = get_spending_summary('month', category=category)
    await update.message.reply_text(report)

@authorized_user
async def help_command(update, context):
    help_text = """
    Expense Tracker Bot Commands:

    Use bot menu button in chat for list of commands.
    /setdivider [symbol] - Set the divider symbol for price (default is $). Example: /setdivider #
    /day [category] - Get spending for today, optionally filter by category.
    /week [category] - Get spending for this week, optionally filter by category.
    /month [category] - Get spending for this month, optionally filter by category.
    /addcat [category name] - Add a new spending category.
    /removecat [category name] - Remove a spending category.
    /editcat [old category] [new category] - Edit a spending category name.
    /categories - Show category buttons to apply category for next expense.
    [Item][Divider][Price] - Send expense in this format to track it. Example: Coffee $10
    /help - Display this help message.

    To edit or delete an expense, reply to your expense message with /edit or /delete command respectively (not implemented yet).
    """
    await update.message.reply_text(help_text)

# --- Category Buttons and Expense Tracking ---
async def category_buttons(update, context):
    categories = sheet.col_values(5) # Get all category cells
    unique_categories = set()
    for cell_value in categories:
        if cell_value:
            for cat in cell_value.split(','):
                unique_categories.add(cat.strip())
    category_list = sorted(list(unique_categories))

    keyboard = []
    row_buttons = []
    for cat in category_list:
        row_buttons.append(InlineKeyboardButton(cat, callback_data=f'set_cat_{cat}'))
        if len(row_buttons) == 3: # Limit to 3 buttons per row for better UI
            keyboard.append(row_buttons)
            row_buttons = []
    if row_buttons: # Add remaining buttons
        keyboard.append(row_buttons)
    keyboard.append([InlineKeyboardButton("No Category", callback_data='set_cat_None')]) # Option for no category

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose a category for your expense:", reply_markup=reply_markup)

async def set_category_callback(update, context):
    query = update.callback_query
    await query.answer()
    category_name = query.data[len('set_cat_'):] # Extract category name from callback data
    context.user_data['current_category'] = category_name if category_name != 'None' else None # Store selected category in user_data
    cat_display_name = category_name if category_name != 'None' else 'No Category'
    await query.edit_message_text(f"Category '{cat_display_name}' selected. Now send your expense (e.g., Item{divider_symbol}Price).")

# --- Message Handler for Expense Tracking ---
@authorized_user
async def track_expense(update, context):
    message_text = update.message.text
    category = context.user_data.get('current_category') # Retrieve selected category
    try:
        parts = message_text.split(divider_symbol)
        if len(parts) != 2:
            raise ValueError("Incorrect format")
        item = parts[0].strip()
        price = float(parts[1].strip())
        write_to_sheet(item, price, category)
        category_display = f" in category '{category}'" if category else ""
        await update.message.reply_text(f'Expense tracked: {item} - {price:.2f}{divider_symbol}{category_display}')
        context.user_data.pop('current_category', None) # Clear category after use
    except ValueError:
        await update.message.reply_text(f'Incorrect format. Please use: Item {divider_symbol}Price (e.g., Coffee {divider_symbol}10)')
    except Exception as e:
        logger.error(f"Error tracking expense: {e}")
        await update.message.reply_text('An error occurred. Please try again.')

# --- Callback Query Handlers ---
async def callback_query_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == 'set_divider_menu':
        await query.edit_message_text("Use /setdivider command to set divider symbol.")
    elif query.data == 'add_category_menu':
        await query.edit_message_text("Use /addcat command to add category.")
    elif query.data == 'remove_category_menu':
        await query.edit_message_text("Use /removecat command to remove category.")
    elif query.data == 'edit_category_menu':
        await query.edit_message_text("Use /editcat command to edit category.")
    elif query.data == 'help':
        await help_command(update, context)
    elif query.data.startswith('set_cat_'):
        await set_category_callback(update, context)
    else:
        await query.edit_message_text(f"Callback query data: {query.data}") # Fallback for unknown callbacks

# --- Error Handler ---
async def error(update, context):
    logger.warning(f'Update {update} caused error {context.error}')
    if update and update.effective_message: # Check if update and effective_message are valid
        await update.effective_message.reply_text(f"An error occurred: `{context.error}`", parse_mode=telegram.constants.ParseMode.MARKDOWN)
    else:
        logger.error(f"Exception while handling an update: {context.error}")


# --- Main Function ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Define bot commands for menu using BotCommand objects
    bot_commands = [
        BotCommand(command='start', description='Start the bot and show bot description and commands'),
        BotCommand(command='help', description='Show help message'),
        BotCommand(command='day', description='Get spending for today'),
        BotCommand(command='week', description='Get spending for this week'),
        BotCommand(command='month', description='Get spending for this month'),
        BotCommand(command='setdivider', description='Set divider symbol'),
        BotCommand(command='addcat', description='Add category'),
        BotCommand(command='removecat', description='Remove category'),
        BotCommand(command='editcat', description='Edit category'),
        BotCommand(command='categories', description='Show category buttons'),
    ]

    # Use set_my_commands to configure the bot's commands menu in Telegram
    try:
        app.bot.set_my_commands(bot_commands)
        logger.info("Bot commands set successfully using setMyCommands.")
    except Exception as e:
        logger.error(f"Failed to set bot commands using setMyCommands: {e}")

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setdivider", set_divider_command))
    app.add_handler(CommandHandler("day", day_spending_command))
    app.add_handler(CommandHandler("week", week_spending_command))
    app.add_handler(CommandHandler("month", month_spending_command))
    app.add_handler(CommandHandler("addcat", add_category))
    app.add_handler(CommandHandler("removecat", remove_category))
    app.add_handler(CommandHandler("editcat", edit_category))
    app.add_handler(CommandHandler("categories", category_buttons))  # Command to show category buttons

    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_expense))

    # Callback query handler
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    app.add_error_handler(error)

    app.run_polling()

if __name__ == '__main__':
    main()