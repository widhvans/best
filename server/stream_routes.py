# server/stream_routes.py (The Final "Download and Serve" Architecture)

import logging
import os
import asyncio
from aiohttp import web
from pyrogram.errors import FileIdInvalid

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()
DOWNLOAD_DIR = "downloads" # Files yahan save hongi

# Sunishchit karein ki download directory maujood hai
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# File download ke liye locks
download_locks = {}


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    bot_username = request.app['bot'].me.username
    return web.json_response({"server_status": "running", "bot_status": f"connected_as @{bot_username}"})


@routes.get("/favicon.ico", allow_head=True)
async def favicon_handler(request):
    return web.Response(status=204)


async def get_file_path(bot, message_id):
    """File ka path prapt karta hai ya use download karta hai."""
    file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}.mp4") # Extension .mp4 maan rahe hain
    
    # Agar file pehle se disk par hai, to uska path return karein
    if os.path.exists(file_path):
        logger.info(f"File {message_id} found in local cache.")
        return file_path

    # Agar file disk par nahi hai, to use download karein
    lock = download_locks.setdefault(message_id, asyncio.Lock())
    async with lock:
        # Dobara check karein, shayad doosre request ne download kar diya ho
        if os.path.exists(file_path):
            return file_path

        logger.info(f"File {message_id} not in cache. Downloading from Telegram...")
        try:
            chat_id = bot.stream_channel_id or bot.owner_db_channel_id
            if not chat_id:
                raise ValueError("Streaming channel not configured.")
            
            message = await bot.get_messages(chat_id, message_id)
            if not message or not message.media:
                raise FileNotFoundError("Message not found or has no media.")
                
            # File ko disk par download karein
            await bot.download_media(message, file_name=file_path)
            logger.info(f"Successfully downloaded {message_id} to {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to download file {message_id}: {e}", exc_info=True)
            # Agar download fail ho to lock release karein taaki dobara try ho sake
            if message_id in download_locks:
                del download_locks[message_id]
            return None


@routes.get("/stream/{message_id:\\d+}", allow_head=True)
@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def stream_and_download_handler(request: web.Request):
    """
    Yeh naya handler pehle file ko download karta hai, fir use disk se serve karta hai.
    """
    bot = request.app['bot']
    message_id = int(request.match_info.get("message_id"))
    
    try:
        file_path = await get_file_path(bot, message_id)
        
        if not file_path:
            return web.Response(status=404, text="File not found or failed to download.")
            
        # aiohttp ka FileResponse istemal karein, jo range requests ko perfectly handle karta hai
        return web.FileResponse(file_path, chunk_size=1024*1024)

    except Exception as e:
        logger.critical(f"FATAL: Error serving file {message_id}: {e}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    """
    Watch page ab sirf links dikhayega. Asli kaam stream_handler karega.
    """
    message_id = request.match_info.get("message_id")
    bot = request.app['bot']

    stream_url = f"http://{bot.vps_ip}:{bot.vps_port}/stream/{message_id}"
    download_url = f"http://{bot.vps_ip}:{bot.vps_port}/download/{message_id}"
    
    # Simple HTML page, iske liye Jinja2 ki bhi zaroorat nahi
    # Agar aapko pehle jaisa page chahiye to template rendering wapas add kar sakte hain
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Watch File</title></head>
    <body>
        <h1>Your file is ready to stream or download.</h1>
        <p><a href="{stream_url}">Watch in External Player</a></p>
        <p><a href="{download_url}">Direct Download</a></p>
    </body>
    </html>
    """
    # Note: Humne online player hata diya hai, jaisa aapne kaha.
    return web.Response(text=html_content, content_type='text/html')
