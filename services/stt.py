import os
from config import Config


def recognize(audio_path):
    provider = Config.STT_PROVIDER
    if provider == "openai":
        return _openai_stt(audio_path)
    elif provider == "gemini":
        return _gemini_stt(audio_path)
    else:
        raise ValueError(f"Unknown STT provider: {provider}")


def _openai_stt(audio_path):
    from openai import OpenAI
    client = OpenAI(api_key=Config.OPENAI_API_KEY)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="en",
        )
    return result.text.strip()


def _gemini_stt(audio_path):
    import google.generativeai as genai
    genai.configure(api_key=Config.GEMINI_API_KEY)
    model = genai.GenerativeModel(Config.GEMINI_MODEL)
    with open(audio_path, "rb") as f:
        audio_data = f.read()
    response = model.generate_content([
        "Transcribe this audio to text. Return only the transcription, nothing else.",
        {"mime_type": "audio/wav", "data": audio_data},
    ])
    return response.text.strip()
