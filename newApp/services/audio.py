import subprocess
import os
import threading
import time

import pygame

from config import Config


pygame.mixer.init()

_recording_process = None
_recording_lock = threading.Lock()


def set_volume(percent):
    level = int(60 + (percent / 100.0) * 67)
    level = max(0, min(127, level))
    card = Config.SOUND_CARD_NAME
    try:
        subprocess.run(
            ["amixer", "-D", f"hw:{card}", "sset", "Speaker", str(level)],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        print("[Audio] amixer not found")


def set_capture_volume(percent=100):
    card = Config.SOUND_CARD_NAME
    try:
        subprocess.run(
            ["amixer", "-D", f"hw:{card}", "sset", "Capture", str(percent)],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        pass


def start_recording(output_path):
    global _recording_process
    with _recording_lock:
        if _recording_process and _recording_process.poll() is None:
            _recording_process.terminate()
            try:
                _recording_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _recording_process.kill()
            _recording_process = None
        card = Config.SOUND_CARD_NAME
        cmd = [
            "arecord", "-D", f"plughw:{card}",
            "-f", "S16_LE", "-r", "16000", "-c", "1",
            output_path,
        ]
        _recording_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"[Audio] Recording started: {output_path}")
        return _recording_process


def stop_recording():
    global _recording_process
    with _recording_lock:
        if _recording_process and _recording_process.poll() is None:
            _recording_process.terminate()
            try:
                _recording_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _recording_process.kill()
            print("[Audio] Recording stopped")
        _recording_process = None


def play_audio_file(file_path, blocking=False):
    if not os.path.exists(file_path):
        print(f"[Audio] File not found: {file_path}")
        return

    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".wav",):
        card = Config.SOUND_CARD_NAME
        cmd = ["aplay", "-D", f"plughw:{card}", file_path]
        if blocking:
            subprocess.run(cmd, capture_output=True)
        else:
            return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    elif ext in (".mp3", ".ogg", ".flac"):
        try:
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            if blocking:
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
        except Exception as e:
            print(f"[Audio] Playback error: {e}")


def play_audio_bytes(audio_bytes, format_hint="wav"):
    tmp_path = f"/tmp/mombot_tts.{format_hint}"
    with open(tmp_path, "wb") as f:
        f.write(audio_bytes)
    play_audio_file(tmp_path, blocking=True)


def stop_playback():
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass


def is_playing():
    try:
        return pygame.mixer.music.get_busy()
    except Exception:
        return False
