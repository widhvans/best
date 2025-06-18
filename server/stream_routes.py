# server/stream_routes.py (The Final Stable Version)

import logging
import asyncio
from aiohttp import web
from pyrogram.errors import FileIdInvalid
from util.render_template import render_page 

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


async def get_media_meta(message_id, bot):
    """
    Ek central function jo file ki details ko cache se laata hai ya zaroorat padne par fetch karke cache karta hai.
    """
    async with bot.cache_lock:
        media_meta = bot.media_cache.get(message_id)
        if not media_meta:
            logger.info(f"Cache MISS for message_id: {message_id}. Fetching from Telegram.")
            chat_id = bot.stream_channel_id or bot.owner_db_channel_id
            if not chat_id:
                raise ValueError("Streaming channels not configured.")
            
            message = await bot.get_messages(chat_id=chat_id, message_ids=message_id)

            if not message or not message.media:
                raise FileIdInvalid(f"Message {message_id} not found or has no media.")
            
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
    return media_meta


async def producer(bot, message_id):
    """
    Dedicated downloader (Producer). File download karke queue mein daalta hai.
    """
    producer_info = bot.stream_producers[message_id]
    try:
        media_meta = await get_media_meta(message_id, bot)
        message = media_meta["message_object"]
        streamer = bot.stream_media(message)
        
        async for chunk in streamer:
            await producer_info['queue'].put(chunk)
            
    except Exception as e:
        logger.error(f"Producer for message {message_id} failed: {e}", exc_info=True)
        await producer_info['queue'].put(None)
    finally:
        await producer_info['queue'].put(None)
        logger.info(f"Producer for message {message_id} has finished.")


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
        
        html_content = await render_page(bot, message_id)
        
        return web.Response(text=html_content, content_type='text/html')
    except Exception as e:
        logger.critical(f"Unexpected error in watch handler: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)


async def stream_or_download(request: web.Request, disposition: str):
    bot = request.app['bot']
    message_id_str = request.match_info.get("message_id")
    try:
        message_id = int(message_id_str)
        
        lock = bot.stream_locks.setdefault(message_id, asyncio.Lock())
        
        async with lock:
            if message_id not in bot.stream_producers:
                queue = asyncio.Queue()
                task = asyncio.create_task(producer(bot, message_id))
                bot.stream_producers[message_id] = {'task': task, 'queue': queue}
                logger.info(f"Started new producer task for message_id: {message_id}")

        producer_info = bot.stream_producers[message_id]
        
        media_meta = await get_media_meta(message_id, bot)
        
        headers = {
            "Content-Type": media_meta["mime_type"],
            "Content-Disposition": f'{disposition}; filename="{media_meta["file_name"]}"',
            "Content-Length": str(media_meta["file_size"])
        }
        
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        
        queue = producer_info['queue']

        while True:
            chunk = await queue.get()
            
            if chunk is None:
                break
            
            try:
                await response.write(chunk)
                await asyncio.sleep(0)
            except (ConnectionError, asyncio.CancelledError):
                logger.warning(f"Consumer for message {message_id} disconnected.")
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
