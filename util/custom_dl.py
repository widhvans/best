import logging
from pyrogram import Client, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import FileMigrate, AuthBytesInvalid
from .file_properties import get_file_properties, FileIdError

logger = logging.getLogger(__name__)

class ByteStreamer:
    def __init__(self, client: Client):
        self.client: Client = client

    async def get_file_properties(self, message_id):
        return await get_file_properties(self.client, message_id)

    @staticmethod
    def get_location(file_id):
        return raw.types.InputDocumentFileLocation(
            id=file_id.media_id,
            access_hash=file_id.access_hash,
            file_reference=file_id.file_reference,
            thumb_size=""
        )

    async def _get_session(self, dc_id: int):
        """
        Helper function to get or create a session for a specific Data Center.
        This is crucial for handling FileMigrate errors.
        """
        session = self.client.media_sessions.get(dc_id)
        
        if session is None:
            # Agar is DC ke liye session nahi hai, to naya banayein
            session = Session(
                self.client, dc_id, await Auth(self.client, dc_id, await self.client.storage.test_mode()).create(),
                await self.client.storage.test_mode(), is_media=True
            )
            await session.start()
            
            try:
                # Naye session ke liye authorization import karein
                exported_auth = await self.client.invoke(
                    raw.functions.auth.ExportAuthorization(dc_id=dc_id)
                )
                await session.invoke(
                    raw.functions.auth.ImportAuthorization(
                        id=exported_auth.id, bytes=exported_auth.bytes
                    )
                )
            except AuthBytesInvalid:
                logger.error(f"Failed to import authorization to DC {dc_id}. This may cause issues.", exc_info=True)

            self.client.media_sessions[dc_id] = session
        
        return session

    async def yield_file(self, file_id, offset, first_part_cut, last_part_cut, part_count, chunk_size):
        location = self.get_location(file_id)
        current_part = 1
        
        # File ke original DC se shuru karein
        media_session = await self._get_session(file_id.dc_id)

        while current_part <= part_count:
            try:
                # Sahi media session par request bhejein
                chunk = await media_session.invoke(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=offset,
                        limit=chunk_size
                    ),
                    retries=0 # Hum manually retry handle karenge
                )
                
                if isinstance(chunk, raw.types.upload.File):
                    if current_part == 1 and part_count > 1:
                        yield chunk.bytes[first_part_cut:]
                    elif current_part == part_count and part_count > 1:
                        yield chunk.bytes[:last_part_cut]
                    else:
                        yield chunk.bytes
                    
                    offset += chunk_size
                    current_part += 1
                else:
                    break
            
            except FileMigrate as e:
                # ================================================================= #
                # VVVVVV YAHAN PAR MAGICAL FIX HAI - FILE MIGRATE ERROR HANDLING VVVVVV #
                # ================================================================= #
                logger.warning(f"File migrated to DC {e.value}. Following file to new DC...")
                
                # Naye DC ke liye naya session prapt karein
                media_session = await self._get_session(e.value)
                
                # Is chunk ko dobara naye session se try karein
                continue
            
            except Exception as e:
                logger.error(f"Could not fetch chunk at offset {offset}: {e}", exc_info=True)
                break
