import jinja2
import aiofiles
from pyrogram import Client
from util.custom_dl import ByteStreamer

async def render_page(bot: Client, message_id: int):
    """Renders the HTML template for the watch page."""
    streamer = ByteStreamer(bot)
    file_name = "Untitled File"
    try:
        # --- FIX: Correctly get message and extract file name ---
        message = await streamer.get_message(message_id)
        if message and message.media:
            media = getattr(message, message.media.value)
            file_name = getattr(media, "file_name", "Untitled File").replace("_", " ")
    except Exception as e:
        # Log the error but continue with a default title
        logging.error(f"Could not get file name for message_id {message_id}: {e}")

    # Construct the stream and download URLs
    stream_url = f"http://{bot.vps_ip}:{bot.vps_port}/stream/{message_id}"
    download_url = f"http://{bot.vps_ip}:{bot.vps_port}/download/{message_id}"
    
    # Read and render the Jinja2 template
    async with aiofiles.open('template/watch_page.html') as f:
        template = jinja2.Template(await f.read())

    return template.render(
        heading=f"Watch {file_name}",
        file_name=file_name,
        stream_url=stream_url,
        download_url=download_url # Pass the new download url to the template
    )
