# server/stream_routes.py (The Final "Master Warehouse" Version)

import logging
import asyncio
from aiohttp import web
from pyrogram.errors import FileIdInvalid

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


async def producer(bot, message_id):
    """
    Yeh ek dedicated downloader hai. Yeh file ko Telegram se download karke 
    use ek shared "warehouse" (cache) mein daalta hai. Yeh har file ke liye sirf ek baar chalta hai.
    """
    # Is producer ko 'stream_producers' mein store karein
    producer_info = bot.stream_producers[message_id]
    
    try:
        # File ki details prapt karein
        chat_id = bot.stream_channel_id or bot.owner_db_channel_id
        message = await bot.get_messages(chat_id, message_id)
        streamer = bot.stream_media(message)
        
        # Chunks download karke queue mein daalein
        async for chunk in streamer:
            await producer_info['queue'].put(chunk)
            
    except Exception as e:
        logger.error(f"Producer for message {message_id} failed: {e}", exc_info=True)
        # Error hone par queue mein None daalein taaki consumers ko pata chale
        await producer_info['queue'].put(None)
    finally:
        # Kaam khatam hone par queue mein None daalein
        await producer_info['queue'].put(None)
        logger.info(f"Producer for message {message_id} has finished.")


async def stream_or_download(request: web.Request, disposition: str):
    """
    Naya streaming handler jo "Master Warehouse" (Producer-Consumer) model ka istemal karta hai.
    """
    bot = request.app['bot']
    message_id_str = request.match_info.get("message_id")
    try:
        message_id = int(message_id_str)
        
        # Har file ke liye ek alag lock prapt karein
        lock = bot.stream_locks.setdefault(message_id, asyncio.Lock())
        
        async with lock:
            # Agar is file ke liye koi producer nahi chal raha hai, to ek naya shuru karein
            if message_id not in bot.stream_producers:
                # Ek nayi queue banayein jismein producer chunks daalega
                queue = asyncio.Queue()
                # Producer ko shuru karein aur use 'stream_producers' mein store karein
                task = asyncio.create_task(producer(bot, message_id))
                bot.stream_producers[message_id] = {'task': task, 'queue': queue}
                logger.info(f"Started new producer task for message_id: {message_id}")

        producer_info = bot.stream_producers[message_id]
        
        # File ki details prapt karein (metadata caching ka istemal karke)
        async with bot.cache_lock:
            media_meta = bot.media_cache.get(message_id)
            if not media_meta:
                chat_id = bot.stream_channel_id or bot.owner_db_channel_id
                message = await bot.get_messages(chat_id, message_id)
                if not message or not message.media:
                    return web.Response(status=404, text="File not found.")
                media = getattr(message, message.media.value)
                media_meta = {
                    "file_name": getattr(media, "file_name", "unknown.dat"),
                    "file_size": int(getattr(media, "file_size", 0))
                }
                bot.media_cache[message_id] = media_meta
        
        headers = {
            "Content-Type": media_meta["mime_type"],
            "Content-Disposition": f'{disposition}; filename="{media_meta["file_name"]}"',
            "Content-Length": str(media_meta["file_size"])
        }
        
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        
        # Is consumer ke liye producer ki queue se ek naya iterator banayein
        consumer_iterator = producer_info['queue'].__aiter__()

        while True:
            try:
                # Warehouse (queue) se agla chunk prapt karein
                chunk = await consumer_iterator.__anext__()
                if chunk is None: # None ka matlab hai ki producer ne download poora kar liya ya fail ho gaya
                    break
                await response.write(chunk)
                await asyncio.sleep(0)
            except (ConnectionError, asyncio.CancelledError):
                logger.warning(f"Consumer for message {message_id} disconnected.")
                break
            except StopAsyncIteration:
                break
        
        return response

    except (FileIdInvalid, ValueError) as e:
        logger.error(f"File ID or configuration error for stream request: {e}")
        return web.Response(status=404, text="File not found or link expired.")
    except Exception:
        logger.critical(f"FATAL: Unexpected error in stream/download handler for message_id={message_id_str}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# Baaki ke handlers stream_or_download ko hi call karenge
@routes.get("/stream/{message_id:\\d+}", allow_head=True)
async def stream_handler(request: web.Request):
    return await stream_or_download(request, "inline")


@routes.get("/download/{message_id:\\d+}", allow_head=True)
async def download_handler(request: web.Request):
    return await stream_or_download(request, "attachment")

# Watch handler ko abhi ke liye simple rakhein
@routes.get("/watch/{message_id:\\d+}", allow_head=True)
async def watch_handler(request: web.Request):
    message_id = request.match_info.get("message_id")
    # Yeh watch page ke liye hai, jo pehle se theek kaam kar raha hai
    # Ismein hum alag se file details le sakte hain taaki page load fast ho
    bot = request.app['bot']
    file_name = "File"
    try:
        async with bot.cache_lock:
             media_meta = bot.media_cache.get(int(message_id))
             if media_meta:
                 file_name = media_meta['file_name']
    except:
        pass
        
    stream_url = f"http://{bot.vps_ip}:{bot.vps_port}/stream/{message_id}"
    download_url = f"http://{bot.vps_ip}:{bot.vps_port}/download/{message_id}"
    from jinja2 import Template
    import aiofiles
    async with aiofiles.open('template/watch_page.html', 'r', encoding='utf-8') as f:
        template_content = await f.read()
    template = Template(template_content)
    return web.Response(text=template.render(heading=f"Watch {file_name}", file_name=file_name, stream_url=stream_url, download_url=download_url), content_type='text/html')
