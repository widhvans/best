# server/stream_routes.py (The Final "Download & Serve" Architecture)

import logging
import os
import asyncio
from aiohttp import web
from pyrogram.errors import FileIdInvalid
from jinja2 import Template
import aiofiles

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()
DOWNLOAD_DIR = "downloads"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


async def get_media_meta(message_id, bot):
    """File ki details prapt karta hai (metadata caching ke saath)."""
    async with bot.cache_lock:
        media_meta = bot.media_cache.get(message_id)
        if not media_meta:
            chat_id = bot.stream_channel_id or bot.owner_db_channel_id
            if not chat_id: raise ValueError("Streaming channels not configured.")
            
            message = await bot.get_messages(chat_id=chat_id, message_ids=message_id)
            if not message or not message.media: raise FileIdInvalid(f"Message {message_id} not found.")
            
            media = getattr(message, message.media.value)
            media_meta = {
                "message_object": message,
                "file_name": getattr(media, "file_name", "unknown.dat"),
                "file_size": int(getattr(media, "file_size", 0)),
            }
            bot.media_cache[message_id] = media_meta
    return media_meta


async def downloader(bot, message_id, file_path):
    """File ko background mein download karta hai."""
    download_info = bot.active_downloads[message_id]
    try:
        message = (await get_media_meta(message_id, bot))["message_object"]
        await bot.download_media(message, file_name=file_path)
        download_info["status"] = "completed"
        logger.info(f"Successfully downloaded {message_id} to {file_path}")
    except Exception as e:
        download_info["status"] = "error"
        logger.error(f"Download failed for {message_id}: {e}", exc_info=True)


@routes.get("/stream/{message_id:\\d+}")
@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def stream_and_download_handler(request: web.Request):
    """
    File ko stream ya download ke liye handle karta hai. Pehle check karta hai ki file disk par hai ya nahi.
    """
    bot = request.app['bot']
    message_id = int(request.match_info.get("message_id"))
    file_path = os.path.join(DOWNLOAD_DIR, str(message_id))

    # Agar file disk par hai, to use seedhe serve karein
    if os.path.exists(file_path):
        logger.info(f"Serving file {message_id} directly from disk.")
        return web.FileResponse(file_path, chunk_size=1024 * 1024)

    # Agar file disk par nahi hai, to user ko progress page par redirect karein
    preparing_url = f"/preparing/{message_id}"
    return web.HTTPFound(preparing_url)


@routes.get("/preparing/{message_id:\\d+}")
async def preparing_page_handler(request: web.Request):
    """
    User ko download progress page dikhata hai.
    """
    bot = request.app['bot']
    message_id = int(request.match_info.get("message_id"))
    
    # Download lock ka istemal karein taaki ek hi baar download shuru ho
    lock = bot.download_locks.setdefault(message_id, asyncio.Lock())
    async with lock:
        if message_id not in bot.active_downloads:
            logger.info(f"No active download for {message_id}. Starting new one.")
            bot.active_downloads[message_id] = {"status": "downloading"}
            asyncio.create_task(downloader(bot, message_id, os.path.join(DOWNLOAD_DIR, str(message_id))))

    # HTML template render karein
    status_url = f"/status/{message_id}"
    final_url = f"/stream/{message_id}" # Download poora hone par is link par redirect hoga
    
    async with aiofiles.open('template/preparing.html', 'r', encoding='utf-8') as f:
        template_content = await f.read()
    template = Template(template_content)
    
    return web.Response(
        text=template.render(status_url=status_url, final_url=final_url),
        content_type='text/html'
    )


@routes.get("/status/{message_id:\\d+}")
async def status_handler(request: web.Request):
    """
    Download ka live status batata hai (JSON format mein).
    """
    bot = request.app['bot']
    message_id = int(request.match_info.get("message_id"))
    
    file_path = os.path.join(DOWNLOAD_DIR, str(message_id))
    download_info = bot.active_downloads.get(message_id)

    if download_info and download_info["status"] == "completed":
        return web.json_response({"status": "completed", "progress": 100})
        
    if download_info and download_info["status"] == "error":
        return web.json_response({"status": "error"})

    if os.path.exists(file_path):
        try:
            current_size = os.path.getsize(file_path)
            media_meta = await get_media_meta(message_id, bot)
            total_size = media_meta["file_size"]
            if total_size > 0:
                progress = int((current_size / total_size) * 100)
                return web.json_response({"status": "downloading", "progress": progress})
        except Exception:
            pass # Agar metadata fetch fail ho to neeche wala response bhej dein
            
    return web.json_response({"status": "downloading", "progress": 0})


@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    """Watch page ab seedhe stream link par redirect kar dega (ya preparing page par)."""
    # Isse user ko hamesha best experience milega
    return web.HTTPFound(f"/stream/{request.match_info.get('message_id')}")
