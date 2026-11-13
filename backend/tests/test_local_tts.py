import io
import sys
import wave

import pytest

from backend import local_tts


def test_local_tts_rejects_unbounded_or_control_text_before_starting_worker():
    for text in ("", " " * 10, "x" * 501, "hello\x00world"):
        with pytest.raises(ValueError):
            local_tts._synthesize(text)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system voices only")
def test_installed_microsoft_voice_produces_bounded_pcm_wave():
    audio = local_tts._synthesize("PANINI local speech check.")
    assert audio.startswith(b"RIFF") and len(audio) <= local_tts.MAX_WAV
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() > wav.getframerate() // 4
