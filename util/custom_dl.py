import asyncio
import logging
import os
import aiofiles
from aiohttp import web
from pyrogram import Client
from pyrogram.types import Message

logger = logging.getLogger(__name__)

# A lock to prevent multiple downloads of the same file at the same time
DOWNLOAD_LOCKS = {}
DOWNLOAD_DIR = "downloads"

class ByteStreamer:
    def __init__(self, client: Client):
        self.client: Client = client

    async def get_message(self, message_id: int) -> Message:
        """Gets the message object from the appropriate channel."""
        stream_channel = self.client.stream_channel_id
        if not stream_channel:
            stream_channel = self.client.owner_db_channel_id
        if not stream_channel:
            raise ValueError("Neither Stream Channel nor Owner DB Channel is configured.")
        return await self.client.get_messages(stream_channel, message_id)

    async def stream_media(self, request: web.Request, message_id: int) -> web.StreamResponse:
        """
        Handles streaming and caching.
        - If file is cached, serves it directly from disk (fast, seekable).
        - If not cached, streams it from Telegram while simultaneously saving to disk.
        """
        try:
            message = await self.get_message(message_id)
            if not message or not message.media:
                raise web.HTTPNotFound(text="File not found or has no media.")
            
            media = getattr(message, message.media.value)
            file_name = getattr(media, "file_name", f"stream_{message_id}")
            file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}_{file_name.replace('/', '_')}")

            # --- Cache Hit: Serve the file directly if it exists ---
            if os.path.exists(file_path):
                logger.info(f"Cache HIT for message_id: {message_id}. Serving from disk.")
                return web.FileResponse(file_path, chunk_size=256*1024)

            # --- Cache Miss: Stream from Telegram and cache to disk ---
            lock = DOWNLOAD_LOCKS.setdefault(message_id, asyncio.Lock())
            async with lock:
                if os.path.exists(file_path):
                    logger.info(f"Cache HIT for message_id: {message_id} (after lock). Serving from disk.")
                    return web.FileResponse(file_path, chunk_size=256*1024)

                logger.info(f"Cache MISS for message_id: {message_id}. Streaming from Telegram and caching.")
                
                response = web.StreamResponse(
                    headers={
                        "Content-Type": getattr(media, "mime_type", "application/octet-stream"),
                        "Content-Disposition": f'inline; filename="{file_name}"',
                    }
                )
                await response.prepare(request)

                temp_file_path = file_path + ".temp"
                
                try:
                    async with aiofiles.open(temp_file_path, "wb") as cache_file:
                        # --- THE CRITICAL FIX IS HERE ---
                        # Use the high-level, reliable stream_media method
                        async for chunk in self.client.stream_media(message):
                            await response.write(chunk)
                            await cache_file.write(chunk)
                    
                    os.rename(temp_file_path, file_path)
                    logger.info(f"Caching complete for message_id: {message_id}")

                except (ConnectionResetError, asyncio.CancelledError):
                    logger.warning(f"Client disconnected during initial stream/cache for message_id: {message_id}.")
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                except Exception as e:
                    logger.exception(f"Error during stream/cache for message_id {message_id}: {e}")
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                
                return response

        except Exception as e:
            logger.exception(f"A critical error occurred in stream_media for message_id {message_id}: {e}")
            raise web.HTTPInternalServerError(text="An internal server error occurred.")
