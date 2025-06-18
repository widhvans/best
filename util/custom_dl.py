import asyncio
import logging
from aiohttp import web
from pyrogram import Client
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, FileIdInvalid
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

    # --- FINALIZED: Producer with correct error handling and loop breaks ---
    async def _producer(
        self,
        response: web.StreamResponse,
        file_prop: FileId,
        from_bytes: int,
        until_bytes: int,
    ):
        chunk_size = 1024 * 1024
        offset = from_bytes - (from_bytes % 4096)
        
        media_session = await self._get_media_session(file_prop.dc_id)
        location = self._get_input_location(file_prop)
        
        bytes_to_yield = until_bytes - from_bytes + 1
        current_offset_cut = from_bytes - offset
        
        try:
            while bytes_to_yield > 0:
                try:
                    chunk = await media_session.invoke(
                        functions.upload.GetFile(location=location, offset=offset, limit=chunk_size)
                    )
                    if not isinstance(chunk, types.upload.File):
                        break

                    # Slice the data to get the exact part we need
                    if current_offset_cut > 0:
                        output_data = chunk.bytes[current_offset_cut:]
                        current_offset_cut = 0
                    else:
                        output_data = chunk.bytes
                    
                    if len(output_data) > bytes_to_yield:
                        output_data = output_data[:bytes_to_yield]

                    await response.write(output_data)

                    bytes_to_yield -= len(output_data)
                    offset += len(chunk.bytes)

                except (ConnectionResetError, asyncio.CancelledError):
                    logger.warning("Client disconnected, stopping producer.")
                    break
                except Exception as e:
                    logger.exception(f"Producer encountered an error: {e}")
                    break
        finally:
            await response.write_eof()

    # --- FINALIZED: Main handler with correct task management ---
    async def stream_media(self, request, message_id: int):
        try:
            file_prop = await self.get_file_properties(message_id)
        except ValueError as e:
            logger.error(f"Configuration error during streaming: {e}")
            return web.Response(status=500, text=str(e))
        
        file_size = file_prop.file_size
        range_header = request.headers.get("Range", f"bytes=0-{file_size-1}")
        
        try:
            from_bytes_str, until_bytes_str = range_header.split("=")[1].split("-")
            from_bytes = int(from_bytes_str)
            until_bytes = int(until_bytes_str) if until_bytes_str else file_size - 1
        except (ValueError, IndexError):
            return web.Response(status=400, text="Invalid Range header")

        if from_bytes >= file_size or until_bytes >= file_size or from_bytes > until_bytes:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})

        req_length = until_bytes - from_bytes + 1
        
        response = web.StreamResponse(
            status=206,
            headers={
                "Content-Type": file_prop.mime_type or "application/octet-stream",
                "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
                "Content-Length": str(req_length),
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'inline; filename="{file_prop.file_name}"'
            }
        )
        await response.prepare(request)
        
        # Start the background task and immediately return the response.
        # The aiohttp server handles the connection from here.
        asyncio.create_task(
            self._producer(response, file_prop, from_bytes, until_bytes)
        )
        
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
