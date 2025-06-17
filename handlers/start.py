import traceback
import logging
from pyrogram import Client, filters, enums
from pyrogram.errors import UserNotParticipant, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import Config
from database.db import add_user, get_file_by_unique_id, get_user, get_owner_db_channel
from utils.helpers import get_main_menu, decode_link
from features.shortener import get_shortlink

logger = logging.getLogger(__name__)

async def send_file(client, user_id, file_unique_id):
    """Helper function to send the final file."""
    try:
        file_data = await get_file_by_unique_id(file_unique_id)
        if not file_data:
            return await client.send_message(user_id, "Sorry, this file is no longer available.")
        
        owner_db_id = await get_owner_db_channel()
        if not owner_db_id:
            logger.error("Owner DB Channel not set, cannot send file.")
            return await client.send_message(user_id, "A configuration error occurred.")

        # --- NEW: Create hyperlinked caption here ---
        owner_settings = await get_user(file_data['owner_id'])
        filename_url = owner_settings.get("filename_url")
        file_name = file_data.get('file_name', 'N/A')
        
        if filename_url:
            caption = f"✅ **Here is your file!**\n\n**[{file_name}]({filename_url})**"
        else:
            caption = f"✅ **Here is your file!**\n\n`{file_name}`"

        await client.copy_message(
            chat_id=user_id,
            from_chat_id=owner_db_id,
            message_id=file_data['file_id'],
            caption=caption
        )
    except Exception:
        logger.exception("Error in send_file function")
        await client.send_message(user_id, "Something went wrong while sending the file.")

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    if message.from_user.is_bot: return
    user_id = message.from_user.id
    await add_user(user_id)
    
    if len(message.command) > 1:
        payload = message.command[1]
        try:
            if payload.startswith("finalget_"):
                _, file_unique_id = payload.split("_", 1)
                await send_file(client, user_id, file_unique_id)
            elif payload.startswith("get_"):
                await handle_file_request(client, message, user_id, payload)
        except Exception:
            logger.exception("Error processing deep link in /start")
            await message.reply_text("Something went wrong.")
    else:
        text = (
            f"Hello {message.from_user.mention}! 👋\n\n"
            "I am your personal **File Storage & Auto-Posting Bot**.\n\n"
            "✓ Save files to private channels.\n"
            "✓ Auto-post them to public channels.\n"
            "✓ Customize everything from captions to footers.\n\n"
            "Click the button below to begin!"
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Let's Go 🚀", callback_data=f"go_back_{user_id}")]]))

async def handle_file_request(client, message, user_id, payload):
    file_unique_id = payload.split("_", 1)[1]
    file_data = await get_file_by_unique_id(file_unique_id)
    if not file_data: return await message.reply_text("File not found or link has expired.")
    owner_id = file_data['owner_id']
    owner_settings = await get_user(owner_id)
    fsub_channel = owner_settings.get('fsub_channel')
    if fsub_channel:
        try:
            await client.get_chat_member(chat_id=fsub_channel, user_id=user_id)
        except UserNotParticipant:
            try: invite_link = await client.export_chat_invite_link(fsub_channel)
            except: invite_link = None
            buttons = [[InlineKeyboardButton("📢 Join Channel", url=invite_link)], [InlineKeyboardButton("🔄 Retry", callback_data=f"retry_{payload}")]]
            return await message.reply_text("You must join the channel to continue.", reply_markup=InlineKeyboardMarkup(buttons))
    
    final_delivery_link = f"https://t.me/{client.me.username}?start=finalget_{file_unique_id}"
    shortened_link = await get_shortlink(final_delivery_link, owner_id)
    buttons = [[InlineKeyboardButton("➡️ Click Here to Get Your File ⬅️", url=shortened_link)]]
    if owner_settings.get("how_to_download_link"):
        buttons.append([InlineKeyboardButton("❓ How to Download", url=owner_settings["how_to_download_link"])])
    await message.reply_text(
        "**Your file is almost ready!**\n\n"
        "1. Click the button above to complete the task.\n"
        "2. You will be automatically redirected back, and I will send you the file.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex(r"^retry_"))
async def retry_handler(client, query):
    await query.message.delete()
    await handle_file_request(client, query.message, query.from_user.id, query.data.split("_", 1)[1])

@Client.on_callback_query(filters.regex(r"go_back_"))
async def go_back_callback(client, query):
    user_id = int(query.data.split("_")[-1])
    if query.from_user.id != user_id: return await query.answer("This is not for you!", show_alert=True)
    try:
        await query.message.edit_text("⚙️ Here are the main settings:", reply_markup=await get_main_menu(user_id))
    except MessageNotModified:
        await query.answer()
