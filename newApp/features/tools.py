from services.audio import set_volume
from features.music_player import music_player

_current_volume = 70

TOOL_DEFINITIONS = [
    {
        "name": "play_song",
        "description": "Play a song from the music library. If no name given, plays a random song.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Song name or partial match. Leave empty for random.",
                }
            },
        },
    },
    {
        "name": "list_songs",
        "description": "List all available songs in the music library.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "stop_music",
        "description": "Stop the currently playing music.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "set_volume",
        "description": "Set the speaker volume level.",
        "parameters": {
            "type": "object",
            "properties": {
                "percent": {
                    "type": "number",
                    "description": "Volume level 0-100",
                }
            },
            "required": ["percent"],
        },
    },
    {
        "name": "increase_volume",
        "description": "Increase the volume by 10%.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "decrease_volume",
        "description": "Decrease the volume by 10%.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "start_game",
        "description": "Start a game. Available games: tic_tac_toe, brick_breaker.",
        "parameters": {
            "type": "object",
            "properties": {
                "game_name": {
                    "type": "string",
                    "description": "Name of the game: tic_tac_toe or brick_breaker",
                }
            },
            "required": ["game_name"],
        },
    },
    {
        "name": "make_game_move",
        "description": "Make a tic-tac-toe move. Positions 1-9 like a numpad (7=top-left, 9=top-right, 1=bottom-left, 3=bottom-right).",
        "parameters": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "number",
                    "description": "Position 1-9 on the tic-tac-toe board (numpad layout)",
                }
            },
            "required": ["position"],
        },
    },
]


def execute_tool(name, args, state_machine):
    global _current_volume

    if name == "play_song":
        result = music_player.play_song(args.get("name"))
        song = music_player.get_current_song_name()
        if song:
            from ui.renderer import display_state
            display_state.update(status="playing music", emoji="🎵", text=f"Now playing: {song}")
            state_machine._set_state("music")
        return result

    elif name == "list_songs":
        return music_player.list_songs()

    elif name == "stop_music":
        return music_player.stop()

    elif name == "set_volume":
        pct = int(args.get("percent", 70))
        pct = max(0, min(100, pct))
        _current_volume = pct
        set_volume(pct)
        return f"Volume set to {pct}%"

    elif name == "increase_volume":
        _current_volume = min(100, _current_volume + 10)
        set_volume(_current_volume)
        return f"Volume increased to {_current_volume}%"

    elif name == "decrease_volume":
        _current_volume = max(0, _current_volume - 10)
        set_volume(_current_volume)
        return f"Volume decreased to {_current_volume}%"

    elif name == "start_game":
        game_name = args.get("game_name", "").lower().replace(" ", "_")
        if game_name == "tic_tac_toe":
            from features.games.tic_tac_toe import TicTacToeGame
            game = TicTacToeGame()
            state_machine._set_state("game", game=game)
            return "Let's play tic-tac-toe! Say your move like 'top left' or 'center'. Long-press to quit."
        elif game_name == "brick_breaker":
            from features.games.brick_breaker import BrickBreakerGame
            game = BrickBreakerGame()
            state_machine._set_state("game", game=game)
            return "Brick breaker time! Press the button to change paddle direction. Long-press to quit."
        else:
            return f"I don't know a game called '{game_name}'. I can play tic_tac_toe or brick_breaker!"

    elif name == "make_game_move":
        if state_machine._active_game and hasattr(state_machine._active_game, "ai_move"):
            pos = int(args.get("position", 5))
            return state_machine._active_game.ai_move(pos)
        return "No tic-tac-toe game is active right now."

    return f"Unknown tool: {name}"
