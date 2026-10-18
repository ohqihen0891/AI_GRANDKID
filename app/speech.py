"""Cantonese speech-to-text (ASR), text-to-speech (TTS) and audio conversion."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import uuid

from .config import S


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def to_ogg_opus(src: str):
    """Convert any audio (webm, mp3, m4a...) to OGG/Opus mono, the WhatsApp voice-note format."""
    if src.endswith(".ogg") and "conv_" in os.path.basename(src):
        return src
    if not has_ffmpeg():
        print("[speech] ffmpeg not found - cannot convert audio. Install ffmpeg.")
        return src if src.endswith(".ogg") else None
    dst = os.path.join(S.MEDIA_DIR, f"conv_{uuid.uuid4().hex[:10]}.ogg")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "16000", dst]
    try:
        subprocess.run(cmd, check=True, timeout=60)
        return dst
    except Exception as e:
        print("[speech] ffmpeg conversion failed:", e)
        return None


# ---------------------------------------------------------------- ASR
def transcribe(path: str):
    """Return Cantonese transcript text, or None if ASR is not configured / failed."""
    provider = S.ASR_PROVIDER.lower()
    if provider == "none" or not path:
        return None
    try:
        if provider == "tencent":
            return _tencent_asr(path)
        if provider == "openai":
            return _openai_asr(path)
    except Exception as e:
        print(f"[speech] ASR failed ({provider}):", e)
    return None


def _tencent_asr(path: str):
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.asr.v20190614 import asr_client, models

    ogg = to_ogg_opus(path) or path
    raw = open(ogg, "rb").read()
    cred = credential.Credential(S.TENCENT_SECRET_ID, S.TENCENT_SECRET_KEY)
    profile = None
    if S.TENCENT_ASR_ENDPOINT:
        hp = HttpProfile()
        hp.endpoint = S.TENCENT_ASR_ENDPOINT
        profile = ClientProfile(httpProfile=hp)
    cli = asr_client.AsrClient(cred, S.TENCENT_REGION, profile) if profile else \
        asr_client.AsrClient(cred, S.TENCENT_REGION)
    req = models.SentenceRecognitionRequest()
    req.from_json_string(json.dumps({
        "EngSerViceType": S.TENCENT_ASR_ENGINE, "SourceType": 1, "VoiceFormat": "ogg-opus",
        "Data": base64.b64encode(raw).decode(), "DataLen": len(raw),
        "ProjectId": 0, "SubServiceType": 2, "UsrAudioKey": "aigrandkid"}))
    resp = cli.SentenceRecognition(req)
    return (resp.Result or "").strip() or None


def _openai_asr(path: str):
    from openai import OpenAI
    cli = OpenAI(base_url=S.OPENAI_ASR_BASE_URL, api_key=S.OPENAI_ASR_API_KEY)
    src = path
    if not path.endswith((".ogg", ".mp3", ".m4a", ".wav", ".webm", ".mp4")):
        src = to_ogg_opus(path) or path
    with open(src, "rb") as f:
        r = cli.audio.transcriptions.create(
            model=S.OPENAI_ASR_MODEL, file=f, language="zh",
            prompt="以下係香港長者講嘅廣東話。")
    return (r.text or "").strip() or None


# ---------------------------------------------------------------- TTS
def tts(text: str):
    """Cantonese voice reply via edge-tts (free). Returns OGG path or None."""
    if not S.TTS_ENABLED:
        return None
    try:
        import edge_tts
    except ImportError:
        print("[speech] edge-tts not installed: pip install edge-tts")
        return None
    mp3 = os.path.join(S.MEDIA_DIR, f"tts_{uuid.uuid4().hex[:10]}.mp3")

    async def _run():
        await edge_tts.Communicate(text, S.TTS_VOICE).save(mp3)

    try:
        asyncio.run(_run())
        return to_ogg_opus(mp3) or mp3
    except Exception as e:
        print("[speech] TTS failed:", e)
        return None
