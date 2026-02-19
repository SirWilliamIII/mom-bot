from services.audio import set_volume
from features.music_player import music_player

_current_volume = 100

# --- Legacy format (OpenAI function calling, used when VOICE_AGENT_MODE=false) ---
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
    {
        "name": "web_search",
        "description": "Search the web for current information. Use when asked about news, weather, facts, or anything you don't know.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        },
    },
]


# --- Voice Agent format (Deepgram function calling) ---
VOICE_AGENT_FUNCTIONS = [
    {
        "name": "play_song",
        "description": "Play a song from the music library. If no name given, plays a random song.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Song name or partial match. Say 'random' for a random song."}
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
        "description": "Set the speaker volume to a specific level.",
        "parameters": {
            "type": "object",
            "properties": {
                "percent": {"type": "number", "description": "Volume level 0-100"}
            },
            "required": ["percent"],
        },
    },
    {
        "name": "increase_volume",
        "description": "Increase the volume by 10 percent.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "decrease_volume",
        "description": "Decrease the volume by 10 percent.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "start_game",
        "description": "Start a game. Available: tic_tac_toe, brick_breaker.",
        "parameters": {
            "type": "object",
            "properties": {
                "game_name": {"type": "string", "description": "tic_tac_toe or brick_breaker"}
            },
            "required": ["game_name"],
        },
    },
    {
        "name": "make_game_move",
        "description": "Make a tic-tac-toe move. Positions 1-9 like numpad (7=top-left, 5=center, 3=bottom-right).",
        "parameters": {
            "type": "object",
            "properties": {
                "position": {"type": "number", "description": "Position 1-9 numpad layout"}
            },
            "required": ["position"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for current information like news, weather, sports scores, facts, or anything you don't know the answer to.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
]


def execute_tool(name, args, state_machine):
    """Execute a tool call from either the legacy LLM or the Voice Agent."""
    global _current_volume

    if name == "play_song":
        result = music_player.play_song(args.get("name"))
        song = music_player.get_current_song_name()
        if song:
            from ui.renderer import display_state
            display_state.update(status="playing music", emoji="🎵", text=f"Now playing: {song}")
            if state_machine and hasattr(state_machine, '_set_state'):
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
            if state_machine and hasattr(state_machine, '_set_state'):
                state_machine._set_state("game", game=game)
            return "Let's play tic-tac-toe! Say your move like 'top left' or 'center'. Long-press to quit."
        elif game_name == "brick_breaker":
            from features.games.brick_breaker import BrickBreakerGame
            game = BrickBreakerGame()
            if state_machine and hasattr(state_machine, '_set_state'):
                state_machine._set_state("game", game=game)
            return "Brick breaker time! Press the button to change paddle direction. Long-press to quit."
        else:
            return f"I don't know a game called '{game_name}'. I can play tic_tac_toe or brick_breaker!"

    elif name == "make_game_move":
        if state_machine and hasattr(state_machine, '_active_game') and state_machine._active_game:
            if hasattr(state_machine._active_game, "ai_move"):
                pos = int(args.get("position", 5))
                return state_machine._active_game.ai_move(pos)
        return "No tic-tac-toe game is active right now."

    elif name == "web_search":
        from features.web_search import search_web
        query = args.get("query", "")
        return search_web(query)

    return f"Unknown tool: {name}"
