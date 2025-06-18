import asyncio
import logging
import os
import aiofiles
import aiohttp
from aiohttp import web
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FileReferenceExpired

logger = logging.getLogger(__name__)

DOWNLOAD_LOCKS = {}
DOWNLOAD_DIR = "downloads"

class ByteStreamer:
    def __init__(self, client: Client):
        self.client: Client = client

    async def get_message(self, message_id: int) -> Message:
        stream_channel = self.client.stream_channel_id
        if not stream_channel:
            stream_channel = self.client.owner_db_channel_id
        if not stream_channel:
            raise ValueError("Neither Stream Channel nor Owner DB Channel is configured.")
        return await self.client.get_messages(stream_channel, message_id)

    async def handle_stream_and_download(self, request: web.Request, message_id: int, disposition: str) -> web.StreamResponse:
        # Is function se outermost try/except hata diya gaya hai taaki errors upar handle ho.
        message = await self.get_message(message_id)
        if not message or not message.media:
            raise web.HTTPNotFound(text="File not found or has no media.")
        
        media = getattr(message, message.media.value)
        file_name = getattr(media, "file_name", f"download_{message_id}")
        file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}_{file_name.replace('/', '_')}")

        if os.path.exists(file_path):
            logger.info(f"Cache HIT for message_id: {message_id}. Serving from disk with disposition: {disposition}")
            return web.FileResponse(
                file_path, 
                chunk_size=256*1024, 
                headers={"Content-Disposition": f'{disposition}; filename="{file_name}"'}
            )

        lock = DOWNLOAD_LOCKS.setdefault(message_id, asyncio.Lock())
        async with lock:
            if os.path.exists(file_path):
                return web.FileResponse(
                    file_path,
                    chunk_size=256*1024,
                    headers={"Content-Disposition": f'{disposition}; filename="{file_name}"'}
                )

            logger.info(f"Cache MISS for message_id: {message_id}. Streaming and caching.")
            
            response = web.StreamResponse(
                headers={
                    "Content-Type": getattr(media, "mime_type", "application/octet-stream"),
                    "Content-Disposition": f'{disposition}; filename="{file_name}"',
                }
            )
            await response.prepare(request)

            temp_file_path = file_path + ".temp"
            
            try:
                async with aiofiles.open(temp_file_path, "wb") as cache_file:
                    async for chunk in self.client.stream_media(message):
                        await response.write(chunk)
                        await cache_file.write(chunk)
                
                os.rename(temp_file_path, file_path)
                logger.info(f"Caching complete for message_id: {message_id}")

            except (asyncio.CancelledError, aiohttp.ClientError, ConnectionError):
                logger.warning(f"Temp file cleanup: Client disconnected during stream for message_id: {message_id}")
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                # Error ko upar bhejein taaki route handler ise pakad sake
                raise
            
            except Exception as e:
                logger.exception(f"Temp file cleanup: Unexpected error during stream for message_id {message_id}: {e}")
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                # Error ko upar bhejein taaki route handler ise pakad sake
                raise
            
            return response
