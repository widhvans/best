import asyncio
import logging
from aiohttp import web
from pyrogram import Client
from pyrogram.session import Session, Auth
from pyrogram.errors import FileReferenceExpired
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
            raise FileReferenceExpired("File not found or media is missing.")

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
            
    @staticmethod
    def _get_input_location(file_id: FileId):
        if file_id.file_type == 'photo':
            return types.InputPhotoFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
        else:
            return types.InputDocumentFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)

    # --- FINALIZED: Stable, linear streaming logic with proper error handling ---
    async def stream_media(self, request: web.Request, message_id: int) -> web.StreamResponse:
        try:
            file_prop = await self.get_file_properties(message_id)
        except Exception as e:
            logger.exception("Could not get file properties for streaming.")
            raise web.HTTPInternalServerError(text=f"Failed to get file properties: {e}")

        file_size = file_prop.file_size
        range_header = request.headers.get("Range", f"bytes=0-{file_size-1 if file_size else 0}")
        
        try:
            from_bytes_str, until_bytes_str = range_header.split("=")[1].split("-")
            from_bytes = int(from_bytes_str)
            until_bytes = int(until_bytes_str) if until_bytes_str else file_size - 1
        except (ValueError, IndexError):
            raise web.HTTPBadRequest(text="Invalid Range header")

        if from_bytes >= file_size or from_bytes > until_bytes:
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{file_size}"})

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

        # Using a conservative and safe chunk size, multiple of 4096
        chunk_size = 256 * 1024
        offset = from_bytes - (from_bytes % 4096)
        first_part_cut = from_bytes - offset
        bytes_to_send = req_length

        try:
            media_session = await self._get_media_session(file_prop.dc_id)
            location = self._get_input_location(file_prop)

            while bytes_to_send > 0:
                try:
                    # Download a chunk from Telegram
                    chunk = await media_session.invoke(
                        functions.upload.GetFile(location=location, offset=offset, limit=chunk_size)
                    )
                except FileReferenceExpired:
                    # This is an expected error, handle it by refreshing the reference
                    logger.warning(f"File reference for message {message_id} expired. Refreshing and retrying.")
                    await asyncio.sleep(1)
                    file_prop = await self.get_file_properties(message_id)
                    location = self._get_input_location(file_prop)
                    # Continue to the next loop iteration to retry the download
                    continue

                if not isinstance(chunk, types.upload.File) or not chunk.bytes:
                    break # End of file from Telegram's side

                # Slice the downloaded chunk to get the exact part we need
                if first_part_cut > 0:
                    output_data = chunk.bytes[first_part_cut:]
                    first_part_cut = 0 # Only apply this to the first chunk
                else:
                    output_data = chunk.bytes

                if len(output_data) > bytes_to_send:
                    output_data = output_data[:bytes_to_send]
                
                # Write the data to the client and handle disconnection
                await response.write(output_data)
                
                bytes_to_send -= len(output_data)
                
                # The offset for the NEXT request must be incremented by the CHUNK_SIZE, not the received length
                offset += chunk_size
                
        except (ConnectionResetError, asyncio.CancelledError):
            logger.warning("Client connection closed.")
        except Exception as e:
            logger.exception(f"An error occurred during file streaming: {e}")
        
        return response
