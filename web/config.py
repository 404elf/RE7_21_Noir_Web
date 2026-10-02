"""Read existing server-owned JSON without accepting filesystem paths from players."""
import json
import math
from pathlib import Path

from match import DEFAULT_TIMER, timer_config
from rules import GAME, rules_checked

ROOT = Path(__file__).resolve().parents[1]


def load_rules(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Game configuration must be an object")
    settings, weights = data.get("game_settings", {}), data.get("trump_weights", {})
    if not isinstance(settings, dict) or not isinstance(weights, dict):
        raise ValueError("Invalid game_settings / trump_weights")
    # Existing presets include explanatory strings and _comment_* entries.
    known = {row[0] for row in GAME}
    rules = rules_checked({
        "game_settings": {k: v for k, v in settings.items() if k in known},
        "trump_weights": {k: v for k, v in weights.items() if not k.startswith("_comment")},
    })
    duration = settings.get("result_screen_duration", 4.0)
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 <= duration <= 60:
        raise ValueError("Invalid result_screen_duration")
    rules["game_settings"]["result_screen_duration"] = duration
    return rules


def load_clock(path, rules):
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Timer configuration must be an object")
        return timer_config(data)
    return {**DEFAULT_TIMER, "settlement_seconds": rules["game_settings"]["result_screen_duration"]}
