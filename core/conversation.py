import time
from config import Config
from core.companion import get_system_prompt


class Conversation:
    def __init__(self):
        self.messages = []
        self.last_activity = time.time()
        self._init_system()

    def _init_system(self):
        self.messages = [
            {"role": "system", "content": get_system_prompt()}
        ]

    def add_user_message(self, text):
        self._check_reset()
        self.messages.append({"role": "user", "content": text})
        self.last_activity = time.time()

    def add_assistant_message(self, text):
        self.messages.append({"role": "assistant", "content": text})
        self.last_activity = time.time()

    def get_messages(self):
        self._check_reset()
        return list(self.messages)

    def _check_reset(self):
        elapsed = time.time() - self.last_activity
        if elapsed > Config.CHAT_HISTORY_RESET_TIME and len(self.messages) > 1:
            print(f"[Conversation] Resetting after {int(elapsed)}s idle")
            self._init_system()

    def reset(self):
        self._init_system()
        self.last_activity = time.time()
