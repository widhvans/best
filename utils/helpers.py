# utils/helpers.py (Full Updated Code)

import re
import base64
import logging
import PTN  # <-- Nayi powerful library import ki gayi
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelInvalid, PeerIdInvalid, ChannelPrivate
from config import Config
from database.db import get_user, remove_from_list
from features.poster import get_poster
from thefuzz import fuzz

logger = logging.getLogger(__name__)

FILES_PER_POST = 20 # Can be adjusted


def clean_filename(name: str):
    """
    Cleans a filename using the parse-torrent-name (PTN) library for superior
    title extraction, while maintaining the required (title, year) return format.
    This new function improves batching, especially for TV shows.
    """
    if not name:
        return "Untitled", None

    try:
        # PTN se behtareen parsing
        parsed_info = PTN.parse(name)
        
        title = parsed_info.get('title', '')
        year = str(parsed_info.get('year')) if parsed_info.get('year') else None

        # TV Shows ke liye special formatting (e.g., "Breaking Bad S01E01")
        if 'season' in parsed_info and 'episode' in parsed_info:
            season = parsed_info.get('season')
            episode = parsed_info.get('episode')
            
            final_title = f"{title} S{str(season).zfill(2)}E{str(episode).zfill(2)}"
            
            episode_name = parsed_info.get('episodeName')
            if episode_name:
                final_title = f"{final_title} - {episode_name}"
            
            return final_title.strip(), year

        # Movies ke liye, sirf title aur saal
        if title:
            return title.strip(), year
        
        # Agar PTN title nahi nikal pata to purane tareeke par fallback karein
        raise ValueError("PTN could not parse a title.")

    except Exception:
        # Agar PTN fail hota hai to purana regex wala tareeka istemal hoga
        logger.warning(f"PTN failed for '{name}', using regex fallback.")
        cleaned_name = re.sub(r'\.\w+$', '', name)
        cleaned_name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', cleaned_name)
        cleaned_name = re.sub(r'[\._\-\|*&^%$#@!()]', ' ', cleaned_name)
        cleaned_name = re.sub(r'[^A-Za-z0-9 ]', '', cleaned_name)
        
        year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_name)
        year_fallback = year_match.group(0) if year_match else None
        if year_fallback: cleaned_name = cleaned_name.replace(year_fallback, '')
        
        tags = ['1080p', '720p', '480p', '2160p', '4k', 'HD', 'FHD', 'UHD', 'BluRay', 'WEBRip', 'WEB-DL', 'HDRip', 'x264', 'x265', 'HEVC', 'AAC', 'Dual Audio', 'Hindi', 'English', 'Esubs', 'Dubbed', r'S\d+E\d+', r'S\d+', r'Season\s?\d+', r'Part\s?\d+', r'E\d+', r'EP\d+', 'COMPLETE', 'WEB-SERIES']
        for tag in tags:
            cleaned_name = re.sub(r'\b' + tag + r'\b', '', cleaned_name, flags=re.I)
        
        final_title = re.sub(r'\s+', ' ', cleaned_name).strip()
        
        return (final_title, year_fallback) if final_title else (re.sub(r'\.\w+$', '', name).replace(".", " "), None)


# --- create_post IS BACK! ---
# This function is now used for creating text-based posts for public channels and the backup feature.
async def create_post(client, user_id, messages):
    user = await get_user(user_id)
    if not user: return []
    first_media_obj = getattr(messages[0], messages[0].media.value, None)
    if not first_media_obj: return []
    primary_title, year = clean_filename(first_media_obj.file_name)
    
    def similarity_sorter(msg):
        media_obj = getattr(msg, msg.media.value, None)
        if not media_obj: return (1.0, "")
        title, _ = clean_filename(media_obj.file_name)
        similarity_score = 1.0 - calculate_title_similarity(primary_title, title)
        natural_key = natural_sort_key(media_obj.file_name)
        return (similarity_score, natural_key)
    messages.sort(key=similarity_sorter)
    
    base_caption_header = f"🎬 **{primary_title} {f'({year})' if year else ''}**"
    post_poster = await get_poster(primary_title, year) if user.get('show_poster', True) else None
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    posts, total = [], len(messages)
    num_posts = (total + FILES_PER_POST - 1) // FILES_PER_POST
    for i in range(num_posts):
        chunk = messages[i*FILES_PER_POST:(i+1)*FILES_PER_POST]
        header = f"{base_caption_header} (Part {i+1}/{num_posts})" if num_posts > 1 else base_caption_header
        links = []
        for m in chunk:
            media = getattr(m, m.media.value, None)
            if not media: continue
            label, _ = clean_filename(media.file_name)
            # Public posts use the /get/ link to drive traffic to the bot
            link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
            links.append(f"📁 `{label or media.file_name}` - [Click Here to Get File]({link})")
        
        final_caption = f"{header}\n\n" + "\n\n".join(links)
        posts.append((post_poster, final_caption, footer_keyboard))
        
    return posts
# --- END create_post ---


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
    # For TV shows like "Title S01E01", the whole thing should be the key
    if re.search(r'S\d+E\d+', cleaned_title, re.I):
        return cleaned_title.rsplit(' ', 2)[0] # "Title S01E01 - Episode Name" -> "Title S01E01"
    
    words = cleaned_title.split()
    if any(re.match(r's\d+', word, re.I) for word in words):
        num_words = 4
    key_words = words[:num_words]
    return " ".join(key_words).lower()

def calculate_title_similarity(title1: str, title2: str) -> float:
    return fuzz.token_sort_ratio(title1, title2) / 100.0

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
