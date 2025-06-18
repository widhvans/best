import asyncio
import logging
from aiohttp import web
from pyrogram import Client
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, FileIdInvalid
from pyrogram.errors.exceptions.bad_request_400 import LimitInvalid
from pyrogram.file_id import FileId
from pyrogram.raw import types, functions

logger = logging.getLogger(__name__)

class ByteStreamer:
    def __init__(self, client: Client):
        self.client: Client = client

    async def get_file_properties(self, message_id: int):
        stream_channel = self.client.stream_channel_id
        if not stream_channel:
            stream_channel = self.client.owner_db_channel_id
        if not stream_channel:
            raise ValueError("Neither Stream Channel nor Owner DB Channel is configured.")
        
        message = await self.client.get_messages(stream_channel, message_id)
        if not message or not message.media:
            raise FileIdInvalid

        media = getattr(message, message.media.value)
        file_id = FileId.decode(media.file_id)
        setattr(file_id, "file_size", media.file_size)
        setattr(file_id, "mime_type", media.mime_type)
        setattr(file_id, "file_name", media.file_name)
        return file_id

    # --- NEW: Asynchronous Producer to pre-fetch chunks from Telegram ---
    async def _producer(
        self,
        response: web.StreamResponse,
        file_prop: FileId,
        from_bytes: int,
        until_bytes: int,
    ):
        """
        This function runs in the background, continuously fetching chunks
        from Telegram and putting them into the response buffer.
        """
        chunk_size = 1024 * 1024  # 1MB
        offset = from_bytes - (from_bytes % 4096)
        
        media_session = await self._get_media_session(file_prop.dc_id)
        location = self._get_input_location(file_prop)
        
        try:
            while offset < until_bytes:
                # Ensure the response is still writable
                if response.is_eof():
                    break
                
                limit = chunk_size
                try:
                    chunk = await media_session.invoke(
                        functions.upload.GetFile(location=location, offset=offset, limit=limit)
                    )
                    if isinstance(chunk, types.upload.File):
                        await response.write(chunk.bytes)
                        offset += limit
                    else:
                        break # End of file or error
                except LimitInvalid:
                    # This should be rare with the fixed limit, but handle it
                    logger.warning("Received LimitInvalid from Telegram, retrying with smaller chunk.")
                    await asyncio.sleep(0.5)
                    continue
                except Exception:
                    # If any other error, stop the producer
                    logger.exception("Producer failed to get chunk from Telegram.")
                    break
        except (asyncio.CancelledError, ConnectionResetError):
            logger.warning("Producer task cancelled or connection reset.")
        finally:
            # Signal that we are done writing
            await response.write_eof()

    async def stream_media(self, request, message_id: int):
        """
        Sets up the streaming response and starts the producer task.
        This function itself does not stream, it manages the process.
        """
        try:
            file_prop = await self.get_file_properties(message_id)
        except ValueError as e:
            logger.error(f"Configuration error during streaming: {e}")
            return web.Response(status=500, text=str(e))
        
        file_size = file_prop.file_size
        range_header = request.headers.get("Range", f"bytes=0-{file_size-1}")
        
        try:
            from_bytes, until_bytes_str = range_header.split("=")[1].split("-")
            from_bytes = int(from_bytes)
            until_bytes = int(until_bytes_str) if until_bytes_str else file_size - 1
        except (ValueError, IndexError):
            return web.Response(status=400, text="Invalid Range header")

        if from_bytes >= file_size or until_bytes >= file_size or from_bytes > until_bytes:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})

        req_length = until_bytes - from_bytes + 1
        
        response = web.StreamResponse(
            status=206, # Always partial content
            headers={
                "Content-Type": file_prop.mime_type or "application/octet-stream",
                "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
                "Content-Length": str(req_length),
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'inline; filename="{file_prop.file_name}"'
            }
        )
        await response.prepare(request)
        
        # Start the background producer task
        producer_task = asyncio.create_task(
            self._producer(response, file_prop, from_bytes, until_bytes)
        )
        
        try:
            # This will wait until the response is finished or cancelled
            await response.wait()
        except asyncio.CancelledError:
            # The client closed the connection, so we stop the producer
            logger.warning("Client connection closed. Cancelling producer task.")
            producer_task.cancel()
        
        # Wait for the producer to finish its cleanup
        await producer_task
        
        return response

    async def _get_media_session(self, dc_id: int):
        session = self.client.media_sessions.get(dc_id)
        if session is None:
            if dc_id != await self.client.storage.dc_id():
                session = Session(self.client, dc_id, await Auth(self.client, dc_id, await self.client.storage.test_mode()).create(), await self.client.storage.test_mode(), is_media=True)
                await session.start()
                exported_auth = await self.client.invoke(functions.auth.ExportAuthorization(dc_id=dc_id))
                await session.invoke(functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))
            else:
                 session = Session(self.client, dc_id, await self.client.storage.auth_key(), await self.client.storage.test_mode(), is_media=True)
                 await session.start()
            self.client.media_sessions[dc_id] = session
        return session
            
    @staticmethod
    def _get_input_location(file_id: FileId):
        if file_id.file_type == 'photo':
            return types.InputPhotoFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
        else:
            return types.InputDocumentFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
