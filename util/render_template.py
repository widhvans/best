# util/render_template.py (NEW FILE)

import logging
import aiofiles
from jinja2 import Template
from pyrogram import Client

# Is module ke liye logger configure karein
logger = logging.getLogger(__name__)

async def render_page(bot: Client, message_id: int) -> str:
    """
    File ki metadata prapt karta hai aur watch_page.html template ko render karta hai.
    """
    file_name = "File"  # Error aane par default naam

    try:
        # File ki details prapt karein
        chat_id = bot.stream_channel_id or bot.owner_db_channel_id
        if not chat_id:
            raise ValueError("Streaming channels are not configured.")

        message = await bot.get_messages(chat_id, message_id)
        if message and message.media:
            media = getattr(message, message.media.value)
            file_name = getattr(media, "file_name", "File")

    except Exception as e:
        logger.error(f"Could not get file properties for watch page (message_id {message_id}): {e}")

    # URLs banayein
    stream_url = f"http://{bot.vps_ip}:{bot.vps_port}/stream/{message_id}"
    download_url = f"http://{bot.vps_ip}:{bot.vps_port}/download/{message_id}"
    
    try:
        # HTML template ko read karein
        async with aiofiles.open('template/watch_page.html', 'r', encoding='utf-8') as f:
            template_content = await f.read()
        
        template = Template(template_content)
        
        # Template mein data daal kar HTML taiyaar karein
        return template.render(
            heading=f"Watch {file_name}",
            file_name=file_name,
            stream_url=stream_url,
            download_url=download_url
        )
    except FileNotFoundError:
        logger.error("FATAL: 'template/watch_page.html' not found. Please ensure the template file exists.")
        return "Internal Server Error: Template file not found."
    except Exception as e:
        logger.error(f"Error rendering Jinja2 template: {e}", exc_info=True)
        return "Internal Server Error: Template rendering failed."
