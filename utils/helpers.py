import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelInvalid
from config import Config
from database.db import get_user, remove_from_list  # Modified import
from features.poster import get_poster
from thefuzz import fuzz

logger = logging.getLogger(__name__)

FILES_PER_POST = 20

# --- NEW FUNCTION ---
async def notify_and_remove_invalid_channel(client, user_id, channel_id, channel_type):
    """
    Checks if a channel is accessible. If not, notifies the user and removes it from DB.
    Returns True if channel is valid, False otherwise.
    """
    try:
        # A lightweight check to see if the bot is in the channel
        await client.get_chat_member(channel_id, client.me.id)
        return True
    except (UserNotParticipant, ChatAdminRequired, ChannelInvalid) as e:
        channel_name = f"ID `{channel_id}`"
        try:
            # Try to get chat title for a friendlier message, might fail if channel is deleted
            chat = await client.get_chat(channel_id)
            channel_name = f"**{chat.title}** (`{channel_id}`)"
        except Exception:
            pass
            
        error_text = (
            f"⚠️ **Channel Inaccessible**\n\n"
            f"Your {channel_type} Channel {channel_name} is no longer accessible. "
            f"The bot may have been kicked, or the channel was deleted.\n\n"
            f"This channel has been automatically removed from your settings to prevent further errors."
        )
        try:
            # Notify the user about the issue
            await client.send_message(user_id, error_text)
            # Remove the invalid channel from the user's settings
            await remove_from_list(user_id, f"{channel_type.lower()}_channels", channel_id)
        except Exception as notify_error:
            logger.error(f"Failed to notify or remove channel for user {user_id}. Error: {notify_error}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while checking channel {channel_id}: {e}")
        return False # Treat unexpected errors as failures to be safe


def calculate_title_similarity(title1: str, title2: str) -> float:
    """Calculates the similarity between two titles using fuzzy matching."""
    return fuzz.token_sort_ratio(title1, title2) / 100.0

def clean_filename(name: str):
    """The master function to clean filenames for poster searching and batching."""
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
    """Creates post(s) with smart formatting, similarity sorting, and automatic splitting."""
    user = await get_user(user_id)
    if not user: return []

    # Get the media object from the first message to determine the primary title
    first_media_obj = getattr(messages[0], messages[0].media.value, None)
    if not first_media_obj: return [] # Safety check
    primary_title, year = clean_filename(first_media_obj.file_name)
    
    # NEW SORTING LOGIC: Sort by similarity to the primary title, then by natural filename sort
    def similarity_sorter(msg):
        media_obj = getattr(msg, msg.media.value, None)
        if not media_obj: return (1.0, "") # Push messages without media to the end
        title, _ = clean_filename(media_obj.file_name)
        similarity_score = 1.0 - calculate_title_similarity(primary_title, title)
        natural_key = natural_sort_key(media_obj.file_name)
        return (similarity_score, natural_key)

    messages.sort(key=similarity_sorter)
    
    base_caption_header = f"🎬 **{primary_title} {f'({year})' if year else ''}**"
    post_poster = await get_poster(primary_title, year) if user.get('show_poster', True) else None
    
    footer_buttons = user.get('footer_buttons', [])
    footer_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons]) if footer_buttons else None
    
    # Smart Formatting for single file
    if len(messages) == 1:
        # FIXED: Correctly get the media object using getattr
        media = getattr(messages[0], messages[0].media.value, None)
        if not media: return []
        
        file_label, _ = clean_filename(media.file_name)
        link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
        caption_body = f"📁 `{file_label or media.file_name}` ({format_bytes(media.file_size)})\n\n[🔗 Click Here to Get File]({link})"
        return [(post_poster, f"{base_caption_header}\n\n{caption_body}", footer_keyboard)]
    
    # Smart Formatting and Splitting for multiple files
    else:
        posts, total = [], len(messages)
        num_posts = (total + FILES_PER_POST - 1) // FILES_PER_POST
        for i in range(num_posts):
            chunk = messages[i*FILES_PER_POST:(i+1)*FILES_PER_POST]
            header = f"{base_caption_header} (Part {i+1}/{num_posts})" if num_posts > 1 else base_caption_header
            
            links = []
            for m in chunk:
                # FIXED: Correctly get the media object using getattr
                media = getattr(m, m.media.value, None)
                if not media: continue
                
                label, _ = clean_filename(media.file_name)
                link = f"http://{Config.VPS_IP}:{Config.VPS_PORT}/get/{media.file_unique_id}"
                links.append(f"📁 `{label or media.file_name}` - [Click Here]({link})")
            
            final_caption = f"{header}\n\n" + "\n\n".join(links)
            posts.append((post_poster, final_caption, footer_keyboard))
        return posts

# --- (The rest of the helper functions are unchanged and provided for completeness) ---

async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings: return InlineKeyboardMarkup([])
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    fsub_text = "⚙️ Manage FSub" if user_settings.get('fsub_channel') else "📢 Set FSub"
    buttons = [
        [InlineKeyboardButton("➕ Manage Auto Post", callback_data="manage_post_ch")],
        [InlineKeyboardButton("🗃️ Manage Index DB", callback_data="manage_db_ch")],
        [InlineKeyboardButton(shortener_text, callback_data="shortener_menu"), InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")],
        [InlineKeyboardButton("🔗 Set Filename Link", callback_data="set_filename_link"), InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")],
        [InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"), InlineKeyboardButton("📂 My Files", callback_data="my_files_1")],
        [InlineKeyboardButton(fsub_text, callback_data="set_fsub")],
        [InlineKeyboardButton("❓ How to Download", callback_data="set_download")]
    ]
    if user_id == Config.ADMIN_ID:
        buttons.append([InlineKeyboardButton("🔑 Set Owner DB", callback_data="set_owner_db")])
        buttons.append([InlineKeyboardButton("⚠️ Reset Files DB", callback_data="reset_db_prompt")])
    return InlineKeyboardMarkup(buttons)

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
