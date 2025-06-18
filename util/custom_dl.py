import asyncio
import logging
import os
import aiofiles
from aiohttp import web
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FileReferenceExpired
from aiohttp.client_exceptions import ClientConnectionResetError
from asyncio import CancelledError

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
        """
        Handles both streaming ('inline') and downloading ('attachment').
        - Validates cached files and serves with range support for both.
        - Streams from Telegram while caching if no valid cache.
        """
        try:
            message = await self.get_message(message_id)
            if not message or not message.media:
                raise web.HTTPNotFound(text="File not found or has no media.")
            
            media = getattr(message, message.media.value)
            file_name = getattr(media, "file_name", f"download_{message_id}")
            file_path = os.path.join(DOWNLOAD_DIR, f"{message_id}_{file_name.replace('/', '_')}")

            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                if file_size == 0:
                    logger.warning(f"Empty cached file for message_id: {message_id}. Removing and re-streaming.")
                    os.remove(file_path)
                else:
                    logger.info(f"Cache HIT for message_id: {message_id}. Serving from disk with disposition: {disposition}")
                    headers = {
                        "Content-Type": getattr(media, "mime_type", "application/octet-stream"),
                        "Content-Disposition": f'{disposition}; filename="{file_name}"',
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(file_size),
                    }

                    range_header = request.headers.get("Range")
                    if range_header:
                        try:
                            start, end = range_header.replace("bytes=", "").split("-")
                            start = int(start) if start else 0
                            end = int(end) if end else file_size - 1
                            if start >= file_size or end >= file_size or start < 0 or end < 0 or start > end:
                                logger.warning(f"Invalid range request for message_id: {message_id}, start: {start}, end: {end}, file_size: {file_size}")
                                headers.update({
                                    "Content-Range": f"bytes */{file_size}",
                                })
                                return web.StreamResponse(status=416, headers=headers)
                            headers.update({
                                "Content-Range": f"bytes {start}-{end}/{file_size}",
                                "Content-Length": str(end - start + 1),
                            })
                            response = web.StreamResponse(status=206, headers=headers)
                            await response.prepare(request)
                            async with aiofiles.open(file_path, "rb") as f:
                                await f.seek(start)
                                remaining = end - start + 1
                                while remaining > 0:
                                    chunk_size = min(256*1024, remaining)
                                    chunk = await f.read(chunk_size)
                                    if not chunk:
                                        break
                                    await response.write(chunk)
                                    remaining -= len(chunk)
                            logger.info(f"Served range {start}-{end} for message_id: {message_id}")
                            return response
                        except ValueError:
                            logger.warning(f"Invalid range header format for message_id: {message_id}, range: {range_header}")
                            headers.update({
                                "Content-Range": f"bytes */{file_size}",
                            })
                            return web.StreamResponse(status=416, headers=headers)
                    else:
                        response = web.StreamResponse(headers=headers)
                        await response.prepare(request)
                        async with aiofiles.open(file_path, "rb") as f:
                            while True:
                                chunk = await f.read(256*1024)
                                if not chunk:
                                    break
                                await response.write(chunk)
                        logger.info(f"Served full file for message_id: {message_id}, size: {file_size}")
                        return response

            lock = DOWNLOAD_LOCKS.setdefault(message_id, asyncio.Lock())
            async with lock:
                if os.path.exists(file_path):
                    return await self.handle_stream_and_download(request, message_id, disposition)

                logger.info(f"Cache MISS for message_id: {message_id}. Streaming and caching.")
                
                response = web.StreamResponse(
                    headers={
                        "Content-Type": getattr(media, "mime_type", "application/octet-stream"),
                        "Content-Disposition": f'{disposition}; filename="{file_name}"',
                        "Accept-Ranges": "bytes",
                    }
                )
                try:
                    await response.prepare(request)
                except (ClientConnectionResetError, ConnectionError):
                    logger.warning(f"Client disconnected before response preparation for message_id: {message_id}")
                    return response

                temp_file_path = file_path + ".temp"
                
                try:
                    async with aiofiles.open(temp_file_path, "wb") as cache_file:
                        async for chunk in self.client.stream_media(message):
                            try:
                                await response.write(chunk)
                                await cache_file.write(chunk)
                            except (ClientConnectionResetError, ConnectionResetError, ConnectionError):
                                logger.warning(f"Client disconnected during streaming for message_id: {message_id}")
                                break
                            except CancelledError:
                                logger.warning(f"Streaming cancelled for message_id: {message_id}")
                                break
                    
                    if os.path.exists(temp_file_path):
                        if os.path.getsize(temp_file_path) > 0:
                            os.rename(temp_file_path, file_path)
                            logger.info(f"Caching complete for message_id: {message_id}")
                        else:
                            logger.warning(f"Empty temp file created for message_id: {message_id}")
                            os.remove(temp_file_path)
                    else:
                        logger.warning(f"Temporary file not found after streaming for message_id: {message_id}")

                except (ConnectionResetError, ConnectionError, CancelledError):
                    logger.warning(f"Client disconnected or stream cancelled during initial stream/cache for message_id: {message_id}")
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                except Exception as e:
                    logger.exception(f"Error during stream/cache for message_id {message_id}: {e}")
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                
                return response

        except Exception as e:
            logger.exception(f"A critical error occurred in handle_stream_and_download for message_id {message_id}: {e}")
            raise web.HTTPInternalServerError(text="An internal server error occurred.")
