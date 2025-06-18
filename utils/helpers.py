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
    """
    Checks if a channel is accessible. If not, notifies the user and removes it from DB.
    Returns True if channel is valid, False otherwise.
    """
    try:
        await client.get_chat_member(channel_id, "me")
        return True
    except (UserNotParticipant, ChatAdminRequired, ChannelInvalid, PeerIdInvalid, ChannelPrivate) as e:
        channel_name = f"`{channel_id}`"
        try:
            chat = await client.get_chat(channel_id)
            channel_name = f"**{chat.title}** (`{channel_id}`)"
        except Exception:
            pass

        error_text = (
            f"⚠️ **Channel Inaccessible**\n\n"
            f"Your {channel_type.title()} Channel {channel_name} is no longer accessible. "
            f"The bot may have been kicked, the channel was deleted, or it lost permissions.\n\n"
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

# --- FINAL IMPROVEMENT: Batching logic is now simpler and more effective ---
def get_title_key(filename: str, num_words: int = 2) -> str:
    """
    Generates a key from the first N (default is 2) significant words of a 
    filename to use for batching. This ensures all episodes of a show are grouped.
    """
    cleaned_title, _ = clean_filename(filename)
    
    words = cleaned_title.split()
    
    # Take the first N words to form the key
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

async def create_post(client, user_id, messages):
    user = await get_user(user_id)
    if not user: return []
    first_media_obj = getattr(messages[0], messages[0].media.value, None)
    if not first_media_obj: return [] 
    primary_title, year = clean_filename(first_media_obj.file_name)
    
    # Sort files naturally within the batch (e.g., episode 1 before 2)
    messages.sort(key=lambda m: natural_sort_key(getattr(m, m.media.value, None).file_name or ""))
    
    base_caption_header = f"🎬 **{primary_title} {f'({year})' if year else ''}**"
    post_poster = await get_poster(primary_title, year) if user.get('show_poster', True) else None
    
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    if len(messages) == 1:
        media = getattr(messages[0], messages[0].media.value, None)
        if not media: return []
        file_label, _ = clean_filename(media.file_name)
        link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        caption_body = f"📁 `{file_label or media.file_name}` ({format_bytes(media.file_size)})\n\n[🔗 Click Here to Get File]({link})"
        return [(post_poster, f"{base_caption_header}\n\n{caption_body}", footer_keyboard)]
    else:
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
                link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
                links.append(f"📁 `{label or media.file_name}` - [Click Here]({link})")
            final_caption = f"{header}\n\n" + "\n\n".join(links)
            posts.append((post_poster, final_caption, footer_keyboard))
        return posts

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

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', s)]
