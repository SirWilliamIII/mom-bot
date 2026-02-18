import os
import tempfile
from config import Config
from services.audio import play_audio_file


def synthesize_and_play(text):
    provider = Config.TTS_PROVIDER
    if provider == "openai":
        return _openai_tts(text)
    elif provider == "gemini":
        return _gemini_tts(text)
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


def synthesize_to_file(text, output_path=None):
    provider = Config.TTS_PROVIDER
    if provider == "openai":
        return _openai_tts_to_file(text, output_path)
    elif provider == "gemini":
        return _gemini_tts_to_file(text, output_path)
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


def _openai_tts(text):
    path = _openai_tts_to_file(text)
    if path:
        play_audio_file(path, blocking=True)


def _openai_tts_to_file(text, output_path=None):
    from openai import OpenAI
    client = OpenAI(api_key=Config.OPENAI_API_KEY)

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)

    response = client.audio.speech.create(
        model="tts-1",
        voice=Config.OPENAI_TTS_VOICE,
        input=text,
    )
    response.stream_to_file(output_path)
    return output_path


def _gemini_tts(text):
    path = _gemini_tts_to_file(text)
    if path:
        play_audio_file(path, blocking=True)


def _gemini_tts_to_file(text, output_path=None):
    import google.generativeai as genai
    genai.configure(api_key=Config.GEMINI_API_KEY)

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content(
        f"Convert this text to speech: {text}",
        generation_config=genai.GenerationConfig(
            response_mime_type="audio/wav",
        ),
    )

    if hasattr(response, "audio") and response.audio:
        with open(output_path, "wb") as f:
            f.write(response.audio)
        return output_path

    print("[TTS] Gemini did not return audio, falling back to text")
    return None


def split_sentences(text):
    sentences = []
    current = ""
    for char in text:
        current += char
        if char in ".!?;\n":
            s = current.strip()
            if len(s) > 2:
                sentences.append(s)
            current = ""
    remaining = current.strip()
    return sentences, remaining
