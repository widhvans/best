import asyncio
import logging
import os
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
        """
        Gets the message object from the appropriate channel.
        This is a more direct approach than creating a FileId object.
        """
        stream_channel = self.client.stream_channel_id
        if not stream_channel:
            stream_channel = self.client.owner_db_channel_id
        if not stream_channel:
            raise ValueError("Neither Stream Channel nor Owner DB Channel is configured.")
        
        return await self.client.get_messages(stream_channel, message_id)

    # --- FINALIZED: The stable, download-and-serve architecture with the correct download call ---
    async def stream_media(self, request: web.Request, message_id: int) -> web.StreamResponse:
        """
        This function now ensures a file is downloaded locally, then serves it.
        This provides a stable and fast streaming experience.
        """
        try:
            # Get the full message object
            message = await self.get_message(message_id)
            if not message or not message.media:
                raise web.HTTPNotFound(text="File not found or has no media.")

            media = getattr(message, message.media.value)
            file_name = getattr(media, "file_name", f"stream_{message_id}")
            file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}_{file_name}")

            # Create a lock for this specific file to prevent simultaneous downloads
            lock = DOWNLOAD_LOCKS.setdefault(message_id, asyncio.Lock())
            
            async with lock:
                # Check if file exists after acquiring the lock
                if not os.path.exists(file_path):
                    logger.info(f"File not found locally. Starting download for message_id: {message_id}")
                    temp_file_path = file_path + ".temp"
                    
                    try:
                        # --- THE CRITICAL FIX IS HERE ---
                        # Pass the entire message object to the downloader.
                        await self.client.download_media(
                            message=message,
                            file_name=temp_file_path
                        )
                        # Rename the file to its final name after successful download
                        os.rename(temp_file_path, file_path)
                        logger.info(f"Download completed for message_id: {message_id}")
                    except Exception as e:
                        logger.exception(f"Failed to download file for message_id {message_id}: {e}")
                        # Clean up temporary file on failure
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
                        raise web.HTTPInternalServerError(text="Failed to download the file from source.")

            # Serve the local file using aiohttp's built-in, efficient FileResponse
            # This handles Range requests, Content-Type, and everything else automatically.
            logger.info(f"Serving file from local path: {file_path}")
            return web.FileResponse(file_path, chunk_size=256*1024)

        except Exception as e:
            logger.exception(f"A critical error occurred in stream_media for message_id {message_id}: {e}")
            raise web.HTTPInternalServerError(text="An internal server error occurred.")
