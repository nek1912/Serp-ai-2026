"""Tests for voice service with fallback chain."""

import pytest
from unittest.mock import AsyncMock, patch
from app.services.voice_service import VoiceService, VoiceUnavailableError


def test_voice_service_import():
    """Test that voice service can be imported without errors."""
    from app.services.voice_service import VoiceService, VoiceUnavailableError
    assert VoiceService is not None
    assert VoiceUnavailableError is not None


def test_voice_service_initialization():
    """Test that voice service initializes with provider objects."""
    service = VoiceService()
    assert hasattr(service, "_sarvam_stt")
    assert hasattr(service, "_sarvam_tts")
    assert hasattr(service, "_azure_stt")
    assert hasattr(service, "_azure_tts")


@pytest.mark.asyncio
async def test_stt_fallback_raises_when_all_providers_disabled():
    """Test that STT raises VoiceUnavailableError when all providers fail."""
    service = VoiceService()

    with patch.object(service._sarvam_stt, 'enabled', False):
        with patch.object(service._azure_stt, 'speech_to_text', new_callable=AsyncMock, side_effect=RuntimeError("Azure failed")):
            with pytest.raises(VoiceUnavailableError) as exc_info:
                await service.speech_to_text(b"test audio", "en")

            assert "No voice providers available" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tts_fallback_raises_when_all_providers_disabled():
    """Test that TTS raises VoiceUnavailableError when both providers are disabled."""
    service = VoiceService()

    with patch.object(service._sarvam_tts, 'enabled', False):
        with patch.object(service._azure_tts, 'enabled', False):
            with pytest.raises(VoiceUnavailableError) as exc_info:
                await service.text_to_speech("Hello world", "en")

            assert "Text-to-speech unavailable" in str(exc_info.value)


@pytest.mark.asyncio
async def test_stt_sarvam_success_skips_azure():
    """Test that STT uses Sarvam when it succeeds, skipping Azure."""
    service = VoiceService()

    with patch.object(service._sarvam_stt, 'enabled', True):
        with patch.object(service._sarvam_stt, 'transcribe', new_callable=AsyncMock, return_value="sarvam result"):
            with patch.object(service._azure_stt, 'speech_to_text', new_callable=AsyncMock) as mock_azure:
                result = await service.speech_to_text(b"test audio", "en")

                assert result == "sarvam result"
                mock_azure.assert_not_called()


@pytest.mark.asyncio
async def test_stt_sarvam_fails_falls_back_to_azure():
    """Test that STT falls back to Azure when Sarvam fails."""
    service = VoiceService()

    with patch.object(service._sarvam_stt, 'enabled', True):
        with patch.object(service._sarvam_stt, 'transcribe', new_callable=AsyncMock, side_effect=RuntimeError("Sarvam failed")):
            with patch.object(service._azure_stt, 'speech_to_text', new_callable=AsyncMock, return_value="azure result"):
                result = await service.speech_to_text(b"test audio", "en")

                assert result == "azure result"


@pytest.mark.asyncio
async def test_stt_sarvam_disabled_falls_back_to_azure():
    """Test that STT falls back to Azure when Sarvam is disabled."""
    service = VoiceService()

    with patch.object(service._sarvam_stt, 'enabled', False):
        with patch.object(service._azure_stt, 'speech_to_text', new_callable=AsyncMock, return_value="azure result"):
            result = await service.speech_to_text(b"test audio", "en")

            assert result == "azure result"


@pytest.mark.asyncio
async def test_tts_sarvam_success_skips_azure():
    """Test that TTS uses Sarvam when it succeeds, skipping Azure."""
    service = VoiceService()

    with patch.object(service._sarvam_tts, 'enabled', True):
        with patch.object(service._sarvam_tts, 'synthesize', new_callable=AsyncMock, return_value=b"sarvam audio"):
            with patch.object(service._azure_tts, 'text_to_speech', new_callable=AsyncMock) as mock_azure:
                result = await service.text_to_speech("Hello world", "en")

                assert result == b"sarvam audio"
                mock_azure.assert_not_called()


@pytest.mark.asyncio
async def test_tts_sarvam_fails_falls_back_to_azure():
    """Test that TTS falls back to Azure when Sarvam fails."""
    service = VoiceService()

    with patch.object(service._sarvam_tts, 'enabled', True):
        with patch.object(service._sarvam_tts, 'synthesize', new_callable=AsyncMock, side_effect=RuntimeError("Sarvam failed")):
            with patch.object(service._azure_tts, 'enabled', True):
                with patch.object(service._azure_tts, 'text_to_speech', new_callable=AsyncMock, return_value=b"azure audio"):
                    result = await service.text_to_speech("Hello world", "en")

                    assert result == b"azure audio"


@pytest.mark.asyncio
async def test_tts_sarvam_empty_falls_back_to_azure():
    """Test that TTS falls back to Azure when Sarvam returns empty audio."""
    service = VoiceService()

    with patch.object(service._sarvam_tts, 'enabled', True):
        with patch.object(service._sarvam_tts, 'synthesize', new_callable=AsyncMock, return_value=b""):
            with patch.object(service._azure_tts, 'enabled', True):
                with patch.object(service._azure_tts, 'text_to_speech', new_callable=AsyncMock, return_value=b"azure audio"):
                    result = await service.text_to_speech("Hello world", "en")

                    assert result == b"azure audio"


@pytest.mark.asyncio
async def test_tts_all_providers_fail_raises_error():
    """Test that TTS raises error when both Sarvam and Azure fail."""
    service = VoiceService()

    with patch.object(service._sarvam_tts, 'enabled', True):
        with patch.object(service._sarvam_tts, 'synthesize', new_callable=AsyncMock, side_effect=RuntimeError("Sarvam failed")):
            with patch.object(service._azure_tts, 'enabled', True):
                with patch.object(service._azure_tts, 'text_to_speech', new_callable=AsyncMock, side_effect=RuntimeError("Azure failed")):
                    with pytest.raises(VoiceUnavailableError) as exc_info:
                        await service.text_to_speech("Hello world", "en")

                    assert "Both Sarvam and Azure failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tts_segments_sarvam_success_skips_azure():
    """Test that TTS segments uses Sarvam when it succeeds, skipping Azure."""
    service = VoiceService()
    segments = [{"text": "Hello", "language": "en"}]

    with patch.object(service._sarvam_tts, 'enabled', True):
        with patch.object(service._sarvam_tts, 'text_to_speech_segments', new_callable=AsyncMock, return_value=b"sarvam audio"):
            with patch.object(service._azure_tts, 'text_to_speech_segments', new_callable=AsyncMock) as mock_azure:
                result = await service.text_to_speech_segments(segments)

                assert result == b"sarvam audio"
                mock_azure.assert_not_called()


@pytest.mark.asyncio
async def test_tts_segments_sarvam_fails_falls_back_to_azure():
    """Test that TTS segments falls back to Azure when Sarvam fails."""
    service = VoiceService()
    segments = [{"text": "Hello", "language": "en"}]

    with patch.object(service._sarvam_tts, 'enabled', True):
        with patch.object(service._sarvam_tts, 'text_to_speech_segments', new_callable=AsyncMock, side_effect=RuntimeError("Sarvam failed")):
            with patch.object(service._azure_tts, 'enabled', True):
                with patch.object(service._azure_tts, 'text_to_speech_segments', new_callable=AsyncMock, return_value=b"azure audio"):
                    result = await service.text_to_speech_segments(segments)

                    assert result == b"azure audio"


@pytest.mark.asyncio
async def test_stt_all_providers_fail():
    """Test that STT raises VoiceUnavailableError when all providers fail."""
    service = VoiceService()

    with patch.object(service._sarvam_stt, 'enabled', True):
        with patch.object(service._sarvam_stt, 'transcribe', new_callable=AsyncMock, side_effect=RuntimeError("Sarvam failed")):
            with patch.object(service._azure_stt, 'speech_to_text', new_callable=AsyncMock, side_effect=RuntimeError("Azure failed")):
                with pytest.raises(VoiceUnavailableError) as exc_info:
                    await service.speech_to_text(b"test audio", "en")

                assert "No voice providers available" in str(exc_info.value)


def test_voice_unavailable_error_is_exception():
    """Test that VoiceUnavailableError is a proper exception."""
    error = VoiceUnavailableError("test message")
    assert isinstance(error, Exception)
    assert str(error) == "test message"
