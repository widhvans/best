import logging
import asyncio
from pyrogram import Client, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import FileMigrate, AuthKeyUnregistered  # Zaroori error import
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
        Pool se session prapt karta hai ya zaroorat padne par ek naya, stable session banata hai.
        """
        session = self.client.media_sessions.get(dc_id)
        
        if session is None:
            logger.info(f"No cached session for DC {dc_id}. Creating a new one...")
            session = Session(
                self.client, dc_id, await Auth(self.client, dc_id, await self.client.storage.test_mode()).create(),
                await self.client.storage.test_mode(), is_media=True
            )
            await session.start()
            self.client.media_sessions[dc_id] = session
            logger.info(f"Successfully created and cached session for DC {dc_id}.")
        
        return session

    async def yield_file(self, file_id, offset, first_part_cut, last_part_cut, part_count, chunk_size):
        """
        Yeh naya 'yield_file' function ab 'AuthKeyUnregistered' aur 'FileMigrate' dono ko handle kar sakta hai.
        """
        location = self.get_location(file_id)
        current_part = 1
        
        # File ke original DC se shuru karein
        dc_id = file_id.dc_id

        while current_part <= part_count:
            # Har chunk ke liye session prapt karein taaki agar purana kharab ho to naya mil sake
            media_session = await self._get_session(dc_id)
            
            try:
                chunk = await media_session.invoke(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=offset,
                        limit=chunk_size
                    ),
                    retries=0
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
            
            # ================================================================= #
            # VVVVVV YAHAN PAR HAI ASLI JAADU - SELF-HEALING LOGIC VVVVVV #
            # ================================================================= #
            
            except AuthKeyUnregistered:
                logger.error(f"Auth key for DC {dc_id} is unregistered. Deleting session from pool and retrying.")
                # Kharab session ko pool se delete karein
                if dc_id in self.client.media_sessions:
                    del self.client.media_sessions[dc_id]
                # Agle loop mein, _get_session function apne aap ek naya, fresh session banayega
                continue
            
            except FileMigrate as e:
                logger.warning(f"File migrated from DC {dc_id} to {e.value}. Switching session.")
                # Agle sabhi chunks ke liye DC ko update karein
                dc_id = e.value
                # Agle loop mein naye DC ke liye session banaya jayega
                continue
            
            except Exception as e:
                logger.error(f"Could not fetch chunk at offset {offset}: {e}", exc_info=True)
                break
