"""Voice service with provider fallback chain.

STT: Sarvam (primary) → Azure (fallback) → text-only
TTS: Sarvam (primary) → Azure (fallback) → error

Each provider has a strict timeout to prevent slow responses from causing chaos.
Providers are called sequentially — never simultaneously.
"""

import asyncio
import logging

from app.providers.sarvam_voice import SarvamSTTProvider, SarvamTTSProvider
from app.providers.azure_voice import AzureVoiceProvider

logger = logging.getLogger(__name__)

# Strict timeouts per provider (seconds)
_STT_TIMEOUT = 15
_TTS_TIMEOUT = 20


class VoiceService:
    """Manages voice providers with sequential fallback and strict timeouts."""

    def __init__(self):
        self._sarvam_stt = SarvamSTTProvider()
        self._sarvam_tts = SarvamTTSProvider()
        self._azure_stt = AzureVoiceProvider()
        self._azure_tts = AzureVoiceProvider()

    async def speech_to_text(self, audio_bytes: bytes, language: str = "en") -> str:
        """Convert speech to text. Sarvam → Azure → error."""
        # Try Sarvam first (best for Indian languages)
        if self._sarvam_stt.enabled:
            try:
                result = await asyncio.wait_for(
                    self._sarvam_stt.transcribe(audio_bytes, language),
                    timeout=_STT_TIMEOUT,
                )
                return result
            except asyncio.TimeoutError:
                logger.warning("Sarvam STT timed out after %ds", _STT_TIMEOUT)
            except Exception as e:
                logger.warning("Sarvam STT failed: %s", e)

        # Fallback to Azure STT
        try:
            result = await asyncio.wait_for(
                self._azure_stt.speech_to_text(audio_bytes, language),
                timeout=_STT_TIMEOUT,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("Azure STT timed out after %ds", _STT_TIMEOUT)
        except Exception as e:
            logger.warning("Azure STT failed: %s", e)

        raise VoiceUnavailableError("No voice providers available. Please type your question.")

    async def text_to_speech(self, text: str, language: str = "en") -> bytes:
        """Convert text to speech. Sarvam (primary) → Azure (fallback)."""
        if self._sarvam_tts.enabled:
            try:
                result = await asyncio.wait_for(
                    self._sarvam_tts.synthesize(text, language),
                    timeout=_TTS_TIMEOUT,
                )
                if result:
                    logger.info("TTS provider: Sarvam")
                    return result
                logger.warning("Sarvam TTS returned empty audio")
            except asyncio.TimeoutError:
                logger.warning("Sarvam TTS timed out after %ds", _TTS_TIMEOUT)
            except Exception as e:
                logger.warning("Sarvam TTS failed: %s", e)

        logger.info("Sarvam TTS failed; falling back to Azure")
        if self._azure_tts.enabled:
            try:
                result = await asyncio.wait_for(
                    self._azure_tts.text_to_speech(text, language),
                    timeout=_TTS_TIMEOUT,
                )
                if result:
                    logger.info("TTS provider: Azure fallback")
                    return result
                logger.warning("Azure TTS returned empty audio")
            except asyncio.TimeoutError:
                logger.warning("Azure TTS timed out after %ds", _TTS_TIMEOUT)
            except Exception as e:
                logger.warning("Azure TTS failed: %s", e)

        raise VoiceUnavailableError(
            "Text-to-speech unavailable. Both Sarvam and Azure failed."
        )

    async def text_to_speech_segments(self, segments: list[dict]) -> bytes:
        """Convert text segments to multi-voice speech. Sarvam (primary) → Azure (fallback)."""
        if self._sarvam_tts.enabled:
            try:
                result = await asyncio.wait_for(
                    self._sarvam_tts.text_to_speech_segments(segments),
                    timeout=_TTS_TIMEOUT,
                )
                if result:
                    logger.info("TTS provider: Sarvam")
                    return result
                logger.warning("Sarvam TTS segments returned empty audio")
            except asyncio.TimeoutError:
                logger.warning("Sarvam TTS segments timed out after %ds", _TTS_TIMEOUT)
            except Exception as e:
                logger.warning("Sarvam TTS segments failed: %s", e)

        logger.info("Sarvam TTS failed; falling back to Azure")
        if self._azure_tts.enabled:
            try:
                result = await asyncio.wait_for(
                    self._azure_tts.text_to_speech_segments(segments),
                    timeout=_TTS_TIMEOUT,
                )
                if result:
                    logger.info("TTS provider: Azure fallback")
                    return result
                logger.warning("Azure TTS segments returned empty audio")
            except asyncio.TimeoutError:
                logger.warning("Azure TTS segments timed out after %ds", _TTS_TIMEOUT)
            except Exception as e:
                logger.warning("Azure TTS segments failed: %s", e)

        raise VoiceUnavailableError(
            "Text-to-speech unavailable. Both Sarvam and Azure failed."
        )


class VoiceUnavailableError(Exception):
    """Raised when all voice providers fail."""
