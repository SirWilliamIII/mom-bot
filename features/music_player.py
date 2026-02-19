import os
import random

from config import Config
from services.audio import play_audio_file, stop_playback, is_playing


class MusicPlayer:
    def __init__(self):
        self.songs = []
        self.current_index = -1
        self.is_playing = False
        self._current_proc = None
        self._scan_music()

    def _scan_music(self):
        music_dir = Config.MUSIC_DIR
        if not os.path.isdir(music_dir):
            print(f"[Music] Directory not found: {music_dir}")
            return
        for f in sorted(os.listdir(music_dir)):
            if f.lower().endswith((".mp3", ".wav", ".ogg", ".flac")):
                self.songs.append({
                    "name": os.path.splitext(f)[0],
                    "path": os.path.join(music_dir, f),
                })
        print(f"[Music] Found {len(self.songs)} songs")

    def list_songs(self):
        if not self.songs:
            return "No songs found in the music folder."
        names = [s["name"] for s in self.songs]
        return "Here are the songs I have: " + ", ".join(names)

    def play_song(self, name=None):
        if not self.songs:
            return "I don't have any songs to play. Add some MP3 files to the music folder!"

        if name:
            name_lower = name.lower()
            for i, s in enumerate(self.songs):
                if name_lower in s["name"].lower():
                    self.current_index = i
                    break
            else:
                return f"I couldn't find a song called '{name}'. Try asking me to list songs!"
        else:
            self.current_index = random.randint(0, len(self.songs) - 1)

        song = self.songs[self.current_index]
        try:
            # Stop any current playback first
            if self._current_proc and self._current_proc.poll() is None:
                stop_playback()
            # play_audio_file handles MP3→WAV conversion and routes through aplay
            self._current_proc = play_audio_file(song["path"], blocking=False)
            self.is_playing = True
            return f"Now playing: {song['name']}"
        except Exception as e:
            print(f"[Music] Error playing {song['path']}: {e}")
            return "Sorry, I couldn't play that song."

    def pause(self):
        # aplay doesn't support pause — stop instead
        if self.is_playing:
            stop_playback()
            self.is_playing = False
            return "Music paused."
        return "No music is playing."

    def resume(self):
        # Re-play current song from the beginning (aplay has no resume)
        if 0 <= self.current_index < len(self.songs):
            return self.play_song(self.songs[self.current_index]["name"])
        return "No song to resume."

    def skip(self):
        if not self.songs:
            return "No songs available."
        self.current_index = (self.current_index + 1) % len(self.songs)
        return self.play_song(self.songs[self.current_index]["name"])

    def stop(self):
        stop_playback()
        self.is_playing = False
        self._current_proc = None
        return "Music stopped."

    def get_current_song_name(self):
        if 0 <= self.current_index < len(self.songs):
            return self.songs[self.current_index]["name"]
        return None


music_player = MusicPlayer()
