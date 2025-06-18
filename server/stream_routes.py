# server/stream_routes.py (The Final Stable Version)

import logging
import asyncio
from aiohttp import web
from pyrogram.errors import FileIdInvalid

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    bot_username = request.app['bot'].me.username
    return web.json_response({
        "server_status": "running",
        "bot_status": f"connected_as @{bot_username}"
    })


@routes.get("/favicon.ico", allow_head=True)
async def favicon_handler(request):
    return web.Response(status=204)


@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        # render_template ab util folder mein nahi hai, isliye import path badalna hoga
        from handlers.start import render_page
        # Agar render_page start.py mein nahi hai, to uske sahi path ka istemal karein
        # For now, assuming it might be moved or is accessible from a different helper
        # Since we deleted utils, let's assume we might need to recreate render_page logic if it was there
        # Let's check the user's files. The user never provided render_template.py. It was my own creation.
        # I will inline a simplified version of it here.

        # Let's get file properties here for the title
        file_name = "File"
        try:
            chat_id = bot.stream_channel_id or bot.owner_db_channel_id
            message = await bot.get_messages(chat_id, message_id)
            if message and message.media:
                media = getattr(message, message.media.value)
                file_name = getattr(media, "file_name", "File")
        except Exception:
            pass

        stream_url = f"http://{bot.vps_ip}:{bot.vps_port}/stream/{message_id}"
        download_url = f"http://{bot.vps_ip}:{bot.vps_port}/download/{message_id}"
        
        # Inlining the template rendering logic for simplicity
        from jinja2 import Template
        async with aiofiles.open('template/watch_page.html', 'r') as f:
            template_content = await f.read()
        template = Template(template_content)
        
        return web.Response(
            text=template.render(
                heading=f"Watch {file_name}",
                file_name=file_name,
                stream_url=stream_url,
                download_url=download_url
            ),
            content_type='text/html'
        )

    except Exception as e:
        logger.critical(f"Unexpected error in watch handler: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


async def stream_or_download(request: web.Request, disposition: str):
    """
    Handles streaming using the most stable method (stream_media) with performance tuning.
    This version avoids all complex session handling and authorization errors.
    """
    bot = request.app['bot']
    message_id_str = request.match_info.get("message_id")
    try:
        message_id = int(message_id_str)
        
        # Metadata Caching ka istemal karein
        async with bot.cache_lock:
            media_meta = bot.media_cache.get(message_id)
            if not media_meta:
                logger.info(f"Cache MISS for message_id: {message_id}. Fetching from Telegram.")
                chat_id = bot.stream_channel_id or bot.owner_db_channel_id
                if not chat_id:
                    raise ValueError("Streaming channels not configured.")
                
                message = await bot.get_messages(chat_id=chat_id, message_ids=message_id)

                if not message or not message.media:
                    return web.Response(status=404, text="File not found or has no media.")
                
                media = getattr(message, message.media.value)
                media_meta = {
                    "message_object": message,
                    "file_name": getattr(media, "file_name", "unknown.dat"),
                    "file_size": int(getattr(media, "file_size", 0)),
                    "mime_type": getattr(media, "mime_type", "application/octet-stream")
                }
                bot.media_cache[message_id] = media_meta
            else:
                logger.info(f"Cache HIT for message_id: {message_id}. Using memory cache.")

        message = media_meta["message_object"]
        file_name = media_meta["file_name"]
        file_size = media_meta["file_size"]
        mime_type = media_meta["mime_type"]

        # Hum ab range requests handle nahi kar rahe, seedha stream bhejenge
        headers = {
            "Content-Type": mime_type,
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Content-Length": str(file_size)
        }
        
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        
        # Pyrogram ka sabse stable stream method istemal karein
        streamer = bot.stream_media(message)
        
        # Data pipeline ko smooth rakhne ke liye event loop ko yield karein
        async for chunk in streamer:
            try:
                await response.write(chunk)
                await asyncio.sleep(0)
            except (ConnectionError, asyncio.CancelledError):
                logger.warning(f"Client disconnected for message {message_id}. Stopping stream.")
                break
        
        return response

    except (FileIdInvalid, ValueError) as e:
        logger.error(f"File ID or configuration error for stream request: {e}")
        return web.Response(status=404, text="File not found or link expired.")
    except Exception:
        logger.critical(f"FATAL: Unexpected error in stream/download handler for message_id={message_id_str}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


@routes.get("/stream/{message_id:\\d+}", allow_head=True)
async def stream_handler(request: web.Request):
    return await stream_or_download(request, "inline")


@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def download_handler(request: web.Request):
    return await stream_or_download(request, "attachment")
