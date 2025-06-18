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
        
    # --- MODIFIED: stream_media logic is now more robust against LIMIT_INVALID ---
    async def stream_media(self, request, message_id: int):
        try:
            file_prop = await self.get_file_properties(message_id)
        except ValueError as e:
            logger.error(f"Configuration error during streaming: {e}")
            return web.Response(status=500, text=str(e))
        
        file_size = file_prop.file_size
        range_header = request.headers.get("Range", f"bytes=0-{file_size-1}")
        
        try:
            from_bytes, until_bytes = (int(x) for x in range_header.split("=")[1].split("-"))
        except (ValueError, IndexError):
            from_bytes, until_bytes = 0, file_size - 1

        if from_bytes > until_bytes or from_bytes < 0 or until_bytes >= file_size:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})

        # Use a chunk size that is a multiple of 1024*4 (Telegram's block size)
        chunk_size = 1024 * 1024  # 1MB
        req_length = until_bytes - from_bytes + 1
        
        response = web.StreamResponse(
            status=206 if from_bytes != 0 else 200,
            headers={
                "Content-Type": file_prop.mime_type or "application/octet-stream",
                "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
                "Content-Length": str(req_length),
                "Content-Disposition": f'attachment; filename="{file_prop.file_name}"' if not str(file_prop.mime_type).startswith("video/") else "inline",
                "Accept-Ranges": "bytes",
            }
        )
        await response.prepare(request)

        media_session = await self._get_media_session(file_prop.dc_id)
        location = self._get_input_location(file_prop)
        
        offset = from_bytes - (from_bytes % 4096) # Align offset to 4096 bytes
        first_part_cut = from_bytes - offset
        bytes_to_yield = req_length

        try:
            while bytes_to_yield > 0:
                # Always request a full, valid chunk size
                limit = chunk_size
                
                chunk = await media_session.invoke(
                    functions.upload.GetFile(location=location, offset=offset, limit=limit)
                )

                if not isinstance(chunk, types.upload.File):
                    break

                # Slice the data to get the exact part we need
                if first_part_cut > 0:
                    output_data = chunk.bytes[first_part_cut:]
                    first_part_cut = 0 # This is only for the first chunk
                else:
                    output_data = chunk.bytes
                
                # Trim the last chunk to the exact size
                if len(output_data) > bytes_to_yield:
                    output_data = output_data[:bytes_to_yield]
                
                await response.write(output_data)

                bytes_to_yield -= len(output_data)
                offset += limit

        except LimitInvalid:
            logger.warning("LimitInvalid received despite fix. This might be a temporary Telegram issue.")
        except (ConnectionError, asyncio.TimeoutError):
            logger.warning("Client connection closed during streaming.")
        except Exception as e:
            logger.error(f"Error while streaming: {e}", exc_info=True)
        finally:
            await response.write_eof()
            return response
    # --- END MODIFIED ---
            
    @staticmethod
    def _get_input_location(file_id: FileId):
        if file_id.file_type == 'photo':
            return types.InputPhotoFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
        else:
            return types.InputDocumentFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
