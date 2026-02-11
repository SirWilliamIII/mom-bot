import os
import random

import pygame

from config import Config
from services.audio import stop_playback


class MusicPlayer:
    def __init__(self):
        self.songs = []
        self.current_index = -1
        self.is_playing = False
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
            pygame.mixer.music.load(song["path"])
            pygame.mixer.music.play()
            self.is_playing = True
            return f"Now playing: {song['name']}"
        except Exception as e:
            print(f"[Music] Error playing {song['path']}: {e}")
            return f"Sorry, I couldn't play that song."

    def pause(self):
        if self.is_playing:
            pygame.mixer.music.pause()
            self.is_playing = False
            return "Music paused."
        return "No music is playing."

    def resume(self):
        pygame.mixer.music.unpause()
        self.is_playing = True
        return "Resuming music."

    def skip(self):
        if not self.songs:
            return "No songs available."
        self.current_index = (self.current_index + 1) % len(self.songs)
        return self.play_song(self.songs[self.current_index]["name"])

    def stop(self):
        stop_playback()
        self.is_playing = False
        return "Music stopped."

    def get_current_song_name(self):
        if 0 <= self.current_index < len(self.songs):
            return self.songs[self.current_index]["name"]
        return None


music_player = MusicPlayer()
