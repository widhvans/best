import jinja2
import aiofiles
from pyrogram import Client

async def render_page(bot: Client, message_id: int):
    """Renders the HTML template for the watch page."""
    # We need to get the file properties to render the template
    from .custom_dl import ByteStreamer
    streamer = ByteStreamer(bot)
    try:
        file_data = await streamer.get_file_properties(message_id)
        file_name = file_data.file_name.replace("_", " ") if file_data.file_name else "Untitled File"
    except Exception:
        file_name = "Untitled File"

    # Construct the stream and download URLs
    stream_url = f"http://{bot.vps_ip}:{bot.vps_port}/stream/{message_id}"
    
    # Read and render the Jinja2 template
    async with aiofiles.open('template/watch_page.html') as f:
        template = jinja2.Template(await f.read())

    return template.render(
        heading=f"Watch {file_name}",
        file_name=file_name,
        stream_url=stream_url
    )
