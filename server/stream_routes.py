# server/stream_routes.py (Full Replacement with Magical Fix)

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
        from util.render_template import render_page
        return web.Response(
            text=await render_page(bot, message_id),
            content_type='text/html'
        )
    except Exception as e:
        logger.critical(f"Unexpected error in watch handler: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


async def stream_or_download(request: web.Request, disposition: str):
    bot = request.app['bot']
    message_id_str = request.match_info.get("message_id")

    try:
        message_id = int(message_id_str)
        
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

        range_header = request.headers.get("Range")
        if range_header:
            from_bytes, until_bytes = 0, file_size - 1
            try:
                range_bytes = range_header.split("=")[1]
                from_bytes = int(range_bytes.split("-")[0])
                if len(range_bytes.split("-")) > 1 and range_bytes.split("-")[1]:
                    until_bytes = int(range_bytes.split("-")[1])
            except (ValueError, IndexError):
                return web.Response(status=400, text="Invalid Range header.")

            if (from_bytes >= file_size) or (until_bytes >= file_size):
                return web.Response(status=416)

            chunk_size = until_bytes - from_bytes + 1
            offset = from_bytes
            status = 206
            headers = {
                "Content-Type": mime_type,
                "Content-Disposition": f'{disposition}; filename="{file_name}"',
                "Content-Length": str(chunk_size),
                "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
                "Accept-Ranges": "bytes"
            }
        else:
            headers = {
                "Content-Type": mime_type,
                "Content-Disposition": f'{disposition}; filename="{file_name}"',
                "Content-Length": str(file_size)
            }
            offset = 0
            status = 200
        
        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)
        
        streamer = bot.stream_media(message, offset=offset)
        
        # ================================================================= #
        # VVVVVV YAHAN PAR MAGICAL FIX LAGAYA GAYA HAI VVVVVV #
        # ================================================================= #
        async for chunk in streamer:
            try:
                await response.write(chunk)
                # Data bhejne ke turant baad, event loop ko doosre kaam karne ka mauka dein.
                # Yeh pipeline ko smooth rakhta hai.
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
