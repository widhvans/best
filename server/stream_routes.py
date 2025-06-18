# server/stream_routes.py (Full Updated Code)

import logging
import asyncio
import os
from aiohttp import web
from pyrogram.errors import FileIdInvalid
# Sahi jagah se render_page import karein
from util.render_template import render_page

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()
DOWNLOAD_DIR = "downloads"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
download_locks = {}


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    bot_username = request.app['bot'].me.username
    return web.json_response({"server_status": "running", "bot_status": f"connected_as @{bot_username}"})


@routes.get("/favicon.ico", allow_head=True)
async def favicon_handler(request):
    return web.Response(status=204)


async def get_file_path(bot, message_id):
    file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}.mp4")
    
    if os.path.exists(file_path):
        logger.info(f"File {message_id} found in local cache.")
        return file_path

    lock = download_locks.setdefault(message_id, asyncio.Lock())
    async with lock:
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
                
            await bot.download_media(message, file_name=file_path)
            logger.info(f"Successfully downloaded {message_id} to {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to download file {message_id}: {e}", exc_info=True)
            if message_id in download_locks:
                del download_locks[message_id]
            return None


@routes.get("/stream/{message_id:\\d+}", allow_head=True)
@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def stream_and_download_handler(request: web.Request):
    bot = request.app['bot']
    message_id = int(request.match_info.get("message_id"))
    
    try:
        file_path = await get_file_path(bot, message_id)
        
        if not file_path:
            return web.Response(status=404, text="File not found or failed to download.")
            
        return web.FileResponse(file_path, chunk_size=1024*1024)

    except Exception as e:
        logger.critical(f"FATAL: Error serving file {message_id}: {e}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    """
    Renders the beautiful watch page UI without the online player.
    """
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        
        # Sahi render_page function ko call karein
        html_content = await render_page(bot, message_id)
        
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        logger.critical(f"Unexpected error in watch handler: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)
