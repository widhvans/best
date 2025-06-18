
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

# Global dictionaries to manage caching and locks
DOWNLOAD_DIR = "downloads"
CACHING_TASKS = {} # Keeps track of files currently being cached
DOWNLOAD_LOCKS = {} # Prevents race conditions for the same file

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

    async def _cache_file_in_background(self, message: Message, file_path: str):
        """
        Downloads a file from Telegram and caches it to disk in a non-blocking background task.
        """
        temp_file_path = file_path + ".temp"
        try:
            logger.info(f"Starting background cache for: {os.path.basename(file_path)}")
            async with aiofiles.open(temp_file_path, "wb") as cache_file:
                async for chunk in self.client.stream_media(message):
                    await cache_file.write(chunk)
            
            os.rename(temp_file_path, file_path)
            logger.info(f"Background cache COMPLETED for: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"Background cache FAILED for {os.path.basename(file_path)}: {e}")
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    async def handle_stream_and_download(self, request: web.Request, message_id: int, disposition: str) -> web.StreamResponse:
        """
        Handles streaming and downloading with a focus on performance.
        - Serves from cache if available.
        - If not, streams directly to the user while caching happens in the background.
        """
        message = await self.get_message(message_id)
        if not message or not message.media:
            raise web.HTTPNotFound(text="File not found or has no media.")
        
        media = getattr(message, message.media.value)
        file_name = getattr(media, "file_name", f"download_{message_id}")
        file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}_{file_name.replace('/', '_')}")

        # 1. CACHE HIT: If file is cached, serve it super fast from disk.
        if os.path.exists(file_path):
            logger.info(f"Cache HIT for message_id: {message_id}. Serving from disk.")
            return web.FileResponse(
                file_path, 
                chunk_size=256*1024, 
                headers={"Content-Disposition": f'{disposition}; filename="{file_name}"'}
            )

        # 2. CACHE MISS: Stream directly to user, and start background caching.
        # This lock ensures the caching task is created only once per file.
        lock = DOWNLOAD_LOCKS.setdefault(message_id, asyncio.Lock())
        async with lock:
            if message_id not in CACHING_TASKS:
                # Start caching in a separate task that does NOT block the main stream.
                task = asyncio.create_task(self._cache_file_in_background(message, file_path))
                CACHING_TASKS[message_id] = task
                
                def remove_task_from_dict(t):
                    CACHING_TASKS.pop(message_id, None)
                    DOWNLOAD_LOCKS.pop(message_id, None)
                    logger.info(f"Caching task for {message_id} finished. Cleaned up.")

                task.add_done_callback(remove_task_from_dict)

        # 3. IMMEDIATE STREAM TO USER: This code runs instantly, without waiting for disk writes.
        logger.info(f"Direct stream for message_id: {message_id}. Caching will happen in background.")
        
        response = web.StreamResponse(
            headers={
                "Content-Type": getattr(media, "mime_type", "application/octet-stream"),
                "Content-Disposition": f'{disposition}; filename="{file_name}"',
                "Content-Length": str(media.file_size) # Important for players
            }
        )
        await response.prepare(request)

        # Stream from Telegram directly to the user's player. NO DISK I/O HERE.
        async for chunk in self.client.stream_media(message):
            await response.write(chunk)
            
        return response
