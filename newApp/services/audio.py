import subprocess
import os
import threading

import pygame

from config import Config


pygame.mixer.init()

_recording_process = None
_recording_lock = threading.Lock()
_active_playback = []
_playback_lock = threading.Lock()


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


# --- File-based recording (legacy mode) ---

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


# --- Streaming audio (Voice Agent mode) ---

def start_recording_stream(sample_rate=16000):
    """Start arecord returning subprocess -- read raw PCM from stdout."""
    card = Config.SOUND_CARD_NAME
    cmd = [
        "arecord", "-D", f"plughw:{card}",
        "-f", "S16_LE", "-r", str(sample_rate), "-c", "1",
        "-t", "raw",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    print(f"[Audio] Streaming recording started ({sample_rate}Hz)")
    return proc


def start_playback_stream(sample_rate=16000):
    """Start aplay returning subprocess -- write raw PCM to stdin."""
    card = Config.SOUND_CARD_NAME
    cmd = [
        "aplay", "-D", f"plughw:{card}",
        "-r", str(sample_rate), "-f", "S16_LE", "-c", "1",
        "-t", "raw",
        "--buffer-time=100000",  # 100ms buffer for low-latency piped playback
    ]
    print(f"[Audio] Starting playback stream: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _track_playback(proc)

    # Check it didn't die immediately
    import time
    time.sleep(0.1)
    if proc.poll() is not None:
        stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        print(f"[Audio] WARNING: aplay exited immediately! rc={proc.returncode} stderr={stderr_out}")
    else:
        print(f"[Audio] Playback stream started ({sample_rate}Hz)")
    return proc


def _track_playback(proc):
    with _playback_lock:
        _active_playback.append(proc)


# --- File-based playback ---

def play_audio_file(file_path, blocking=False):
    if not os.path.exists(file_path):
        print(f"[Audio] File not found: {file_path}")
        return

    card = Config.SOUND_CARD_NAME
    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".mp3", ".ogg", ".flac"):
        wav_path = file_path + ".wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", file_path, "-ar", "48000", "-ac", "2", wav_path],
                capture_output=True, timeout=30,
            )
            file_path = wav_path
        except Exception as e:
            print(f"[Audio] ffmpeg convert failed: {e}")
            return

    cmd = ["aplay", "-D", f"plughw:{card}", file_path]
    print(f"[Audio] Playing: {file_path}")
    if blocking:
        subprocess.run(cmd, capture_output=True)
    else:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _track_playback(proc)
        return proc


def play_audio_bytes(audio_bytes, format_hint="wav"):
    tmp_path = f"/tmp/mombot_tts.{format_hint}"
    with open(tmp_path, "wb") as f:
        f.write(audio_bytes)
    play_audio_file(tmp_path, blocking=True)


# --- Playback control ---

def stop_playback():
    """Kill all active playback (aplay subprocesses + pygame mixer)."""
    with _playback_lock:
        for proc in _active_playback:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        _active_playback.clear()

    try:
        subprocess.run(["pkill", "-f", "aplay"], capture_output=True, timeout=2)
    except Exception:
        pass

    try:
        pygame.mixer.music.stop()
    except Exception:
        pass


def is_playing():
    with _playback_lock:
        _active_playback[:] = [p for p in _active_playback if p.poll() is None]
        if _active_playback:
            return True
    try:
        return pygame.mixer.music.get_busy()
    except Exception:
        return False
