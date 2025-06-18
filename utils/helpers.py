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

FILES_PER_POST = 20

async def notify_and_remove_invalid_channel(client, user_id, channel_id, channel_type):
    try:
        await client.get_chat_member(channel_id, "me")
        return True
    except (UserNotParticipant, ChatAdminRequired, ChannelInvalid, PeerIdInvalid, ChannelPrivate):
        channel_name = f"`{channel_id}`"
        try:
            chat = await client.get_chat(channel_id)
            channel_name = f"**{chat.title}** (`{channel_id}`)"
        except Exception: pass
        error_text = (
            f"⚠️ **Channel Inaccessible**\n\n"
            f"Your {channel_type.title()} Channel {channel_name} is no longer accessible. "
            f"The bot may have been kicked, or the channel was deleted.\n\n"
            f"This channel has been automatically removed from your settings to prevent further errors."
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

def clean_filename(name: str):
    if not name: return "Untitled", None
    cleaned_name = re.sub(r'\.\w+$', '', name)
    cleaned_name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', cleaned_name)
    cleaned_name = re.sub(r'[\._\-\|*&^%$#@!()]', ' ', cleaned_name)
    cleaned_name = re.sub(r'\b\d{4}\b', '', cleaned_name) # Remove year early
    cleaned_name = re.sub(r'[^A-Za-z0-9 ]', '', cleaned_name)
    year_match = re.search(r'\b(19|20)\d{2}\b', name) # Get year from original name
    year = year_match.group(0) if year_match else None
    tags = ['1080p', '720p', '480p', '2160p', '4k', 'HD', 'FHD', 'UHD', 'BluRay', 'WEBRip', 'WEB-DL', 'HDRip', 'x264', 'x265', 'HEVC', 'AAC', 'Dual Audio', 'Hindi', 'English', 'Esubs', 'Dubbed', r'S\d+E\d+', r'S\d+', r'Season\s?\d+', r'Part\s?\d+', r'E\d+', r'EP\d+', 'COMPLETE', 'WEB-SERIES', 'NF', 'AMZN', 'DDP5', 'ESub', 'VP9', 'AV1', 'MULTi', 'COMBINED']
    for tag in tags:
        cleaned_name = re.sub(r'\b' + tag + r'\b', '', cleaned_name, flags=re.I)
    final_title = re.sub(r'\s+', ' ', cleaned_name).strip()
    return (final_title, year) if final_title else (re.sub(r'\.\w+$', '', name).replace(".", " "), None)

# --- NEW: Advanced Batch Key Generation ---
def generate_batch_key(filename: str) -> tuple:
    """
    Generates a sophisticated key for grouping files based on the most
    significant words in the filename.
    """
    cleaned_title, _ = clean_filename(filename)
    if not cleaned_title:
        return None

    words = cleaned_title.split()
    # Filter out short, insignificant words (e.g., 'a', 'is', '1', 'it')
    significant_words = [word for word in words if len(word) > 2 and not word.isdigit()]
    
    # If no significant words found, fall back to first two words
    if not significant_words:
        significant_words = words[:2]
        if not significant_words:
            return None

    # Sort words by length, longest first
    significant_words.sort(key=len, reverse=True)
    
    # Take the top 2 longest words as the primary key components
    # Using 2 is more stable for titles with varying secondary words.
    key_components = significant_words[:2]
    
    # Sort alphabetically to ensure consistency (e.g., "Rana Naidu" and "Naidu Rana" produce the same key)
    key_components.sort(key=str.lower)
    
    # The key is a tuple of the lowercase words
    batch_key = tuple(word.lower() for word in key_components)
    
    return batch_key

# --- UPDATED: create_post now accepts the batch key to generate a header ---
async def create_post(client, user_id, messages, batch_key_words=None):
    """Creates post(s) with smart formatting, sorting, and header generation."""
    user = await get_user(user_id)
    if not user or not messages: return []

    # --- New Header Generation Logic ---
    search_title = ""
    search_year = None
    if batch_key_words:
        # Use the provided key words to create a clean, consistent header
        header_title = " ".join(word.capitalize() for word in batch_key_words)
        # Still get the year from the first filename for poster searching
        _, search_year = clean_filename(getattr(messages[0], messages[0].media.value).file_name)
        search_title = header_title
    else:
        # Fallback for single files or if key is not provided
        first_media_obj = getattr(messages[0], messages[0].media.value, None)
        if not first_media_obj: return [] 
        header_title, search_year = clean_filename(first_media_obj.file_name)
        search_title = header_title
    
    base_caption_header = f"🎬 **{header_title} {f'({search_year})' if search_year else ''}**"
    
    # --- The rest of the function remains the same ---
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', s)]
    
    # Sort messages naturally for logical order (e.g., episode 1, 2, 10)
    messages.sort(key=lambda m: natural_sort_key(getattr(m, m.media.value, type('obj', (object,), {'file_name': ''})()).file_name))
    
    post_poster = await get_poster(search_title, search_year) if user.get('show_poster', True) else None
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    posts, total = [], len(messages)
    if total == 0: return []

    if total == 1:
        media = getattr(messages[0], messages[0].media.value, None)
        if not media: return []
        file_label, _ = clean_filename(media.file_name)
        link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        caption_body = f"📁 `{file_label or media.file_name}` ({format_bytes(media.file_size)})\n\n[🔗 Click Here to Get File]({link})"
        return [(post_poster, f"{base_caption_header}\n\n{caption_body}", footer_keyboard)]
    else:
        num_posts = (total + FILES_PER_POST - 1) // FILES_PER_POST
        for i in range(num_posts):
            chunk = messages[i*FILES_PER_POST:(i+1)*FILES_PER_POST]
            header = f"{base_caption_header} (Part {i+1}/{num_posts})" if num_posts > 1 else base_caption_header
            links = []
            for m in chunk:
                media = getattr(m, m.media.value, None)
                if not media: continue
                label, _ = clean_filename(media.file_name)
                link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
                links.append(f"📁 `{label or media.file_name}` - [Click Here]({link})")
            final_caption = f"{header}\n\n" + "\n\n".join(links)
            posts.append((post_poster, final_caption, footer_keyboard))
        return posts

# The rest of helpers.py is unchanged but included for completeness
async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings: 
        return "Could not find your settings.", InlineKeyboardMarkup([])
    menu_text = "⚙️ **Bot Settings**\n\nChoose an option below to configure the bot."
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    if user_settings.get('fsub_channel'):
        fsub_text = "⚙️ Manage FSub"
        fsub_callback = "fsub_menu"
    else:
        fsub_text = "📢 Set FSub"
        fsub_callback = "set_fsub"
    buttons = [
        [InlineKeyboardButton("🗂️ Manage Channels", callback_data="manage_channels_menu")],
        [InlineKeyboardButton(shortener_text, callback_data="shortener_menu"), InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")],
        [InlineKeyboardButton("✍️ Filename Link", callback_data="filename_link_menu"), InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")],
        [InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"), InlineKeyboardButton("📂 My Files", callback_data="my_files_1")],
        [InlineKeyboardButton(fsub_text, callback_data=fsub_callback)],
        [InlineKeyboardButton("❓ How to Download", callback_data="set_download")]
    ]
    if user_id == Config.ADMIN_ID:
        buttons.append([InlineKeyboardButton("🔑 Set Owner DB", callback_data="set_owner_db")])
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
