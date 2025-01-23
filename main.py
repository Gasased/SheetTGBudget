# --- Telegram Bot Script ---
import telegram
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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

# --- Divider Symbol (configurable via bot) ---
divider_symbol = '$'

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

# --- Function to write data to Google Sheets ---
def write_to_sheet(item, price):
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M:%S")
    next_row = len(list(filter(None, sheet.col_values(1)))) + 1  # Find next empty row
    row_values = [date, time, item, price]
    sheet.insert_row(row_values, next_row)

# --- Function to get spending summary ---
def get_spending_summary(period: str) -> float:
    """
    Calculates the total spending for a given period (day, week, or month) from a Google Sheet.
    Returns: float: Total spent or 0 if the data is invalid or no data found.
    """
    try:
        raw_data: List[List[str]] = sheet.get_all_values()  # Get all values, including headers
        logging.info(f"Raw data fetched from the sheet: {raw_data}")
    except Exception as e:
        logging.error(f"Failed to fetch data from the sheet: {e}")
        return 0.0

    # Validate data
    if not raw_data:
        logging.warning("No data available in the sheet.")
        return 0.0

    # Since we don't expect headers, we'll treat all rows as data
    rows = raw_data

    today = datetime.date.today()
    total_spent = 0.0

    try:
        if period == 'day':
            logging.info("Calculating spending for today.")
            for row in rows:
                try:
                    date_str = row[0].strip()  # Assuming 'date' is the first column
                    price_str = row[3].strip()  # Assuming 'price' is the fourth column
                    
                    # Attempt to parse the date, handling potential parsing issues
                    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

                    if date_obj == today:
                        total_spent += float(price_str)
                except (ValueError, IndexError) as e:
                    logging.warning(f"Skipping invalid row {row}: {e}")

        elif period == 'week':
            logging.info("Calculating spending for the week.")
            start_of_week = today - datetime.timedelta(days=today.weekday())
            for row in rows:
                try:
                   date_str = row[0].strip()  # Assuming 'date' is the first column
                   price_str = row[3].strip()  # Assuming 'price' is the fourth column
                   date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                   if start_of_week <= date_obj <= today:
                       total_spent += float(price_str)
                except (ValueError, IndexError) as e:
                    logging.warning(f"Skipping invalid row {row}: {e}")

        elif period == 'month':
            logging.info("Calculating spending for the month.")
            for row in rows:
                try:
                    date_str = row[0].strip()  # Assuming 'date' is the first column
                    price_str = row[3].strip()  # Assuming 'price' is the fourth column
                    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_obj.year == today.year and date_obj.month == today.month:
                        total_spent += float(price_str)
                except (ValueError, IndexError) as e:
                   logging.warning(f"Skipping invalid row {row}: {e}")

        else:
            logging.warning(f"Invalid period specified: {period}")
    except Exception as e:
        logging.error(f"Error calculating spending summary for period '{period}': {e}")
        return 0.0

    logging.info(f"Total spent for '{period}': {total_spent}")
    return total_spent



# --- Command Handlers ---
async def start(update, context):
    await update.message.reply_text('Hello! I am your expense tracker bot. Send me expenses like "Coffee $10".')

async def set_divider(update, context):
    global divider_symbol
    if context.args:
        new_divider = context.args[0]
        if len(new_divider) == 1:  # Ensure the divider is a single character
            divider_symbol = new_divider
            await update.message.reply_text(f'Divider symbol set to: {divider_symbol}')
        else:
            await update.message.reply_text('Divider symbol must be a single character.')
    else:
        await update.message.reply_text('Please provide a divider symbol. For example: /setdivider #')

async def day_spending(update, context):
    spent = get_spending_summary('day')
    await update.message.reply_text(f'Spent today: {spent:.2f}{divider_symbol}')

async def week_spending(update, context):
    spent = get_spending_summary('week')
    await update.message.reply_text(f'Spent this week: {spent:.2f}{divider_symbol}')

async def month_spending(update, context):
    spent = get_spending_summary('month')
    await update.message.reply_text(f'Spent this month: {spent:.2f}{divider_symbol}')

# --- Message Handler for Expense Tracking ---
async def track_expense(update, context):
    message_text = update.message.text
    try:
        parts = message_text.split(divider_symbol)
        if len(parts) != 2:
            raise ValueError("Incorrect format")
        item = parts[0].strip()
        price = float(parts[1].strip())
        write_to_sheet(item, price)
        await update.message.reply_text(f'Expense tracked: {item} - {price:.2f}{divider_symbol}')
    except ValueError:
        await update.message.reply_text(f'Incorrect format. Please use: Item {divider_symbol}Price (e.g., Coffee $10)')
    except Exception as e:
        logging.error(f"Error tracking expense: {e}")
        await update.message.reply_text('An error occurred. Please try again.')

async def help_command(update, context):
    help_text = """
    Expense Tracker Bot Commands:

    /start - Start the bot and get a welcome message.
    /setdivider [symbol] - Set the divider symbol for price (default is $). Example: /setdivider #
    /day - Get spending for today.
    /week - Get spending for this week.
    /month - Get spending for this month.
    [Item][Divider][Price] - Send expense in this format to track it. Example: Coffee $10
    /help - Display this help message.
    """
    await update.message.reply_text(help_text)

# --- Error Handler ---
async def error(update, context):
    logging.error(f'Update {update} caused error {context.error}')

# --- Main Function ---
def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
    )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setdivider", set_divider))
    app.add_handler(CommandHandler("day", day_spending))
    app.add_handler(CommandHandler("week", week_spending))
    app.add_handler(CommandHandler("month", month_spending))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_expense))

    app.add_error_handler(error)

    app.run_polling()

if __name__ == '__main__':
    main()
