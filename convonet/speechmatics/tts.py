"""
Speechmatics Text-to-Speech (preview API).

Uses the same Bearer token as batch STT: SPEECHMATICS_API_KEY.

Voice: SPEECHMATICS_TTS_VOICE — sarah | theo | megan | jack (default sarah).
Output: WAV 16 kHz mono (browser-friendly with mime audio/wav).

If the optional package ``speechmatics-tts`` is installed, synthesis uses the
official AsyncClient + OutputFormat.WAV_16000; otherwise falls back to HTTP
(POST https://preview.tts.speechmatics.com/generate/<voice>).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_PREVIEW_TTS_BASE = "https://preview.tts.speechmatics.com/generate"


def _allowed_voice() -> str:
    v = (os.getenv("SPEECHMATICS_TTS_VOICE") or "sarah").strip().lower()
    if v in ("sarah", "theo", "megan", "jack"):
        return v
    logger.warning("SPEECHMATICS_TTS_VOICE=%r invalid; using sarah", v)
    return "sarah"


def _synthesize_http(text: str, api_key: str) -> Optional[bytes]:
    voice = _allowed_voice()
    url = f"{_PREVIEW_TTS_BASE}/{voice}"
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"text": text},
            timeout=120,
        )
        if r.status_code != 200:
            logger.error(
                "Speechmatics TTS HTTP failed: %s %s",
                r.status_code,
                (r.text or "")[:500],
            )
            return None
        data = r.content
        if not data or len(data) < 100:
            logger.error("Speechmatics TTS: empty or tiny response")
            return None
        logger.info("Speechmatics TTS (HTTP): %s bytes voice=%s", len(data), voice)
        return data
    except Exception as e:
        logger.exception("Speechmatics TTS HTTP error: %s", e)
        return None


async def _synthesize_sdk_async(text: str) -> bytes:
    from speechmatics.tts import AsyncClient, OutputFormat, Voice

    voice_id = _allowed_voice()
    voice = getattr(Voice, voice_id.upper(), Voice.SARAH)

    async with AsyncClient() as client:
        async with await client.generate(
            text=text,
            voice=voice,
            output_format=OutputFormat.WAV_16000,
        ) as response:
            parts: list[bytes] = []
            async for chunk in response.content.iter_chunked(65536):
                parts.append(chunk)
            return b"".join(parts)


def synthesize_speechmatics_tts(text: str) -> Optional[bytes]:
    """
    Return WAV bytes (16 kHz) or None on failure.
    """
    if not text or not str(text).strip():
        return None
    api_key = (os.getenv("SPEECHMATICS_API_KEY") or "").strip()
    if not api_key:
        logger.error("SPEECHMATICS_API_KEY not set (required for Speechmatics TTS)")
        return None

    try:
        import speechmatics.tts  # noqa: F401

        try:
            out = asyncio.run(_synthesize_sdk_async(text))
            if out and len(out) >= 100:
                logger.info(
                    "Speechmatics TTS (SDK): %s bytes voice=%s",
                    len(out),
                    _allowed_voice(),
                )
                return out
        except Exception as e:
            logger.warning("Speechmatics TTS SDK failed, using HTTP: %s", e)
    except ImportError:
        logger.debug("speechmatics-tts not installed; using HTTP TTS")

    return _synthesize_http(text, api_key)
