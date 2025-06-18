import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelInvalid, PeerIdInvalid, ChannelPrivate
from config import Config
from database.db import get_user, remove_from_list
from features.poster import get_poster
from thefuzz import fuzz

logger = logging.getLogger(__name__)

async def notify_and_remove_invalid_channel(client, user_id, channel_id, channel_type):
    try:
        await client.get_chat_member(channel_id, "me")
        return True
    except (UserNotParticipant, ChatAdminRequired, ChannelInvalid, PeerIdInvalid, ChannelPrivate):
        channel_name = f"`{channel_id}`"
        try:
            chat = await client.get_chat(channel_id)
            channel_name = f"**{chat.title}** (`{channel_id}`)"
        except Exception:
            pass
        error_text = (
            f"⚠️ **Channel Inaccessible**\n\n"
            f"Your {channel_type.title()} Channel {channel_name} is no longer accessible. "
            f"This channel has been automatically removed from your settings."
        )
        try:
            await client.send_message(user_id, error_text, parse_mode='md')
            db_key = f"{channel_type.lower()}_channels"
            await remove_from_list(user_id, db_key, channel_id)
        except Exception as notify_error:
            logger.error(f"Failed to notify or remove channel for user {user_id}. Error: {notify_error}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while checking channel {channel_id}: {e}")
        return False

def get_title_key(filename: str, num_words: int = 3) -> str:
    cleaned_title, _ = clean_filename(filename)
    words = cleaned_title.split()
    if any(re.match(r's\d+', word, re.I) for word in words):
        num_words = 4
    key_words = words[:num_words]
    return " ".join(key_words).lower()

def calculate_title_similarity(title1: str, title2: str) -> float:
    return fuzz.token_sort_ratio(title1, title2) / 100.0

def clean_filename(name: str):
    if not name: return "Untitled", None
    cleaned_name = re.sub(r'\.\w+$', '', name)
    cleaned_name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', cleaned_name)
    cleaned_name = re.sub(r'[\._\-\|*&^%$#@!()]', ' ', cleaned_name)
    cleaned_name = re.sub(r'[^A-Za-z0-9 ]', '', cleaned_name)
    year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_name)
    year = year_match.group(0) if year_match else None
    if year: cleaned_name = cleaned_name.replace(year, '')
    tags = ['1080p', '720p', '480p', '2160p', '4k', 'HD', 'FHD', 'UHD', 'BluRay', 'WEBRip', 'WEB-DL', 'HDRip', 'x264', 'x265', 'HEVC', 'AAC', 'Dual Audio', 'Hindi', 'English', 'Esubs', 'Dubbed', r'S\d+E\d+', r'S\d+', r'Season\s?\d+', r'Part\s?\d+', r'E\d+', r'EP\d+', 'COMPLETE', 'WEB-SERIES']
    for tag in tags:
        cleaned_name = re.sub(r'\b' + tag + r'\b', '', cleaned_name, flags=re.I)
    final_title = re.sub(r'\s+', ' ', cleaned_name).strip()
    return (final_title, year) if final_title else (re.sub(r'\.\w+$', '', name).replace(".", " "), None)

async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings:
        return "Could not find your settings.", InlineKeyboardMarkup([])
    
    menu_text = "⚙️ **Bot Settings**\n\nChoose an option below to configure the bot."
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    fsub_text = "⚙️ Manage FSub" if user_settings.get('fsub_channel') else "📢 Set FSub"
    fsub_callback = "fsub_menu" if user_settings.get('fsub_channel') else "set_fsub"
    
    buttons = [
        [InlineKeyboardButton("🗂️ Manage Channels", callback_data="manage_channels_menu")],
        [InlineKeyboardButton(shortener_text, callback_data="shortener_menu"), InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")],
        [InlineKeyboardButton("✍️ Filename Link", callback_data="filename_link_menu"), InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")],
        [InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"), InlineKeyboardButton("📂 My Files", callback_data="my_files_1")],
        [InlineKeyboardButton(fsub_text, callback_data=fsub_callback), InlineKeyboardButton("❓ How to Download", callback_data="set_download")]
    ]
    
    if user_id == Config.ADMIN_ID:
        admin_buttons = [
            InlineKeyboardButton("🔑 Set Owner DB", callback_data="set_owner_db"),
            InlineKeyboardButton("🌊 Set Stream Channel", callback_data="set_stream_ch")
        ]
        buttons.append(admin_buttons)
        buttons.append([InlineKeyboardButton("⚠️ Reset Files DB", callback_data="reset_db_prompt")])
        
    keyboard = InlineKeyboardMarkup(buttons)
    return menu_text, keyboard

def go_back_button(user_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]])

def format_bytes(size):
    if not isinstance(size, (int, float)): return "N/A"
    power = 1024; n = 0; power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power and n < len(power_labels) - 1:
        size /= power; n += 1
    return f"{size:.2f} {power_labels[n]}"

async def get_file_raw_link(message):
    return f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.id}"

def encode_link(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().strip("=")

def decode_link(encoded_text: str) -> str:
    padding = 4 - (len(encoded_text) % 4)
    encoded_text += "=" * padding
    return base64.urlsafe_b64decode(encoded_text).decode()

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', s)]
