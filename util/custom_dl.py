import math
import asyncio
import logging
from aiohttp import web  # <-- FIX: Added the missing import
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
        """Fetches file properties from the stream channel, with Owner DB as a fallback."""
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

    async def stream_media(self, request, message_id: int):
        try:
            file_prop = await self.get_file_properties(message_id)
        except ValueError as e:
            logger.error(f"Configuration error during streaming: {e}")
            return web.Response(status=500, text=str(e))
            
        file_size = file_prop.file_size

        range_header = request.headers.get("Range")
        from_bytes, until_bytes = (0, file_size - 1)

        if range_header:
            try:
                range_val = range_header.strip().split("=")[1]
                from_bytes, until_bytes_val = map(str.strip, range_val.split("-"))
                from_bytes = int(from_bytes)
                until_bytes = int(until_bytes_val) if until_bytes_val else file_size - 1
            except (ValueError, IndexError):
                return web.Response(status=400, text="Invalid Range header")

        if from_bytes > until_bytes or from_bytes < 0 or until_bytes >= file_size:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})

        chunk_size = 256 * 1024
        req_length = until_bytes - from_bytes + 1
        
        response = web.StreamResponse(
            status=206 if range_header else 200,
            headers={
                "Content-Type": file_prop.mime_type or "application/octet-stream",
                "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
                "Content-Length": str(req_length),
                "Content-Disposition": f'attachment; filename="{file_prop.file_name}"' if not str(file_prop.mime_type).startswith("video/") else "inline",
                "Accept-Ranges": "bytes"
            }
        )
        await response.prepare(request)

        media_session = await self._get_media_session(file_prop.dc_id)
        location = self._get_input_location(file_prop)
        offset = from_bytes - (from_bytes % chunk_size)
        first_part_cut = from_bytes - offset
        
        try:
            while offset <= until_bytes:
                limit = chunk_size
                if offset + limit > until_bytes:
                    limit = until_bytes - offset + 1
                chunk = await media_session.invoke(functions.upload.GetFile(location=location, offset=offset, limit=limit))
                if not isinstance(chunk, types.upload.File): break
                if first_part_cut:
                    await response.write(chunk.bytes[first_part_cut:])
                    first_part_cut = 0
                else:
                    await response.write(chunk.bytes)
                offset += limit
        except (ConnectionError, asyncio.TimeoutError):
            pass
        except Exception as e:
            logger.error(f"Error while streaming: {e}", exc_info=True)
        finally:
            await response.write_eof()
            return response
            
    @staticmethod
    def _get_input_location(file_id: FileId):
        if file_id.file_type == 'photo':
            return types.InputPhotoFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
        else:
            return types.InputDocumentFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
