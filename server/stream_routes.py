# server/stream_routes.py (The Final "Byte-Level Translator" Version)

import logging
import asyncio
from aiohttp import web
from pyrogram.errors import FileIdInvalid
from util.render_template import render_page 

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


async def get_media_meta(message_id, bot):
    """
    A central function to get file details from cache or fetch and cache them.
    """
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
                "mime_type": getattr(media, "mime_type", "application/octet-stream")
            }
            bot.media_cache[message_id] = media_meta
    return media_meta


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    bot_username = request.app['bot'].me.username
    return web.json_response({"server_status": "running", "bot_status": f"connected_as @{bot_username}"})


@routes.get("/favicon.ico", allow_head=True)
async def favicon_handler(request):
    return web.Response(status=204)


@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app['bot']
        html_content = await render_page(bot, message_id)
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        logger.critical(f"Unexpected error in watch handler: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


async def stream_handler_controller(request: web.Request, disposition: str):
    """
    The master streaming controller with manual offset alignment.
    This is the definitive fix for external player buffering.
    """
    bot = request.app['bot']
    message_id_str = request.match_info.get("message_id")
    try:
        message_id = int(message_id_str)
        
        media_meta = await get_media_meta(message_id, bot)
        message = media_meta["message_object"]
        file_size = media_meta["file_size"]
        
        range_header = request.headers.get("Range")
        
        headers = {
            "Content-Type": media_meta["mime_type"],
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'{disposition}; filename="{media_meta["file_name"]}"'
        }
        
        if range_header:
            from_bytes, until_bytes = 0, file_size - 1
            try:
                range_spec = range_header.split("=")[1]
                from_bytes_str, until_bytes_str = range_spec.split("-")
                from_bytes = int(from_bytes_str)
                if until_bytes_str: until_bytes = int(until_bytes_str)
            except (ValueError, IndexError):
                return web.Response(status=400, text="Invalid Range header")

            if from_bytes >= file_size or until_bytes >= file_size:
                return web.Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})

            bytes_to_send = until_bytes - from_bytes + 1
            headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"
            headers["Content-Length"] = str(bytes_to_send)
            status_code = 206
        else:
            from_bytes = 0
            bytes_to_send = file_size
            headers["Content-Length"] = str(file_size)
            status_code = 200

        response = web.StreamResponse(status=status_code, headers=headers)
        await response.prepare(request)
        
        # ================================================================= #
        # VVVVVV YAHAN HAI ASLI JAADU - BYTE-LEVEL TRANSLATOR LOGIC VVVVVV #
        # ================================================================= #
        
        # Telegram se hamesha 4096 ke multiple par hi data maangein
        block_size = 4096
        aligned_offset = (from_bytes // block_size) * block_size
        bytes_to_skip = from_bytes - aligned_offset

        downloader = bot.stream_media(message, offset=aligned_offset)

        bytes_sent = 0
        first_chunk = True
        async for chunk in downloader:
            if not chunk: break

            if first_chunk:
                chunk = chunk[bytes_to_skip:]
                first_chunk = False

            if bytes_sent + len(chunk) > bytes_to_send:
                chunk = chunk[:bytes_to_send - bytes_sent]
            
            try:
                await response.write(chunk)
                bytes_sent += len(chunk)
            except (ConnectionError, asyncio.CancelledError):
                logger.warning(f"Client disconnected for message {message_id}.")
                break
            
            if bytes_sent >= bytes_to_send:
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
    return await stream_handler_controller(request, "inline")


@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def download_handler(request: web.Request):
    return await stream_handler_controller(request, "attachment")
