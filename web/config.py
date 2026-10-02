"""Read existing server-owned JSON without accepting filesystem paths from players."""
import json
import math
import copy
from pathlib import Path

from cards import CARDS
from match import DEFAULT_TIMER, UNLIMITED_TIMER_FIELDS, timer_config
from presentation_rules import DEFAULT_WEIGHTS, NUMBER_NAMES
from rules import GAME, rules_checked

ROOT = Path(__file__).resolve().parents[1]
WEIGHT_KEYS = {"Return+" if name == "ADD2+" else name for name in CARDS if name not in NUMBER_NAMES}
TIMER_FIELDS = [
    ("turn_seconds", "每次行动秒数", 1, 3600),
    ("round_seconds", "每人每局秒数", 1, 86400),
    ("initial_minutes", "整场初始分钟", 1, 1440),
    ("increment_seconds", "抽牌 / 停牌增加秒数", 0, 300),
    ("preparation_seconds", "首次行动准备秒数", 1, 3600),
    ("settlement_seconds", "亮牌等待秒数（0 为跳过）", 0, 60),
]


def room_rules(value, defaults):
    """Normalize desktop JSON into a complete, bounded, room-owned rule set."""
    if not isinstance(value, dict) or any(k not in {"game_settings", "trump_weights"}
                                          and not k.startswith("_comment") for k in value):
        raise ValueError("规则需要包含 game_settings / trump_weights 对象。")
    settings = {row[0]: defaults["game_settings"][row[0]] for row in GAME}
    weights = {key: defaults["trump_weights"].get(key, DEFAULT_WEIGHTS.get(key, 0))
               for key in WEIGHT_KEYS}
    incoming = value.get("game_settings", {})
    pool = value.get("trump_weights", {})
    if not isinstance(incoming, dict) or not isinstance(pool, dict):
        raise ValueError("游戏参数和牌池必须是对象。")
    duration = defaults["game_settings"].get("result_screen_duration", 4.)
    for key, number in incoming.items():
        if key.startswith("_comment") or key == "num_limit" and isinstance(number, str):
            continue  # Old presets contain this explanatory string.
        if key not in settings and key != "result_screen_duration":
            raise ValueError(f"未知游戏参数：{key}")
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError(f"游戏参数必须是有限数字：{key}")
        if key == "result_screen_duration":
            if not 0 <= number <= 60:
                raise ValueError("结算等待需为 0–60 秒。")
            duration = number
        else:
            settings[key] = number
    for key, number in pool.items():
        if key.startswith("_comment") or key.startswith(("_name_", "_c_")) and isinstance(number, str):
            continue
        if key not in WEIGHT_KEYS:
            raise ValueError(f"未知王牌：{key}")
        if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 10000:
            raise ValueError(f"王牌权重需为 0–10000 的有限数字：{key}")
        weights[key] = number
    try:
        checked = rules_checked({"game_settings": settings, "trump_weights": weights})
    except ValueError as exc:
        raise ValueError("游戏参数超出允许范围，牌堆至少需要 4 个不同点数。") from exc
    return {"game_settings": {**{key: checked["game_settings"][key] for key in settings},
                              "result_screen_duration": duration}, "trump_weights": weights}


def room_timer(value, defaults):
    if not isinstance(value, dict):
        raise ValueError("计时配置必须是对象。")
    result = copy.deepcopy(defaults)
    allowed = {"enabled", "mode", *(row[0] for row in TIMER_FIELDS)}
    for key, number in value.items():
        if key.startswith("_comment"):
            continue
        if key not in allowed:
            raise ValueError(f"未知计时参数：{key}")
        if key == "enabled":
            if type(number) is not bool:
                raise ValueError("计时开关必须为 true / false。")
        elif key == "mode":
            if number not in ("turn", "round", "fischer"):
                raise ValueError("计时方式需为 turn / round / fischer。")
        elif number is None and key in UNLIMITED_TIMER_FIELDS:
            pass
        else:
            row = next(row for row in TIMER_FIELDS if row[0] == key)
            if type(number) not in (int, float) or not math.isfinite(number) or not row[2] <= number <= row[3]:
                raise ValueError(f"{row[1]}需为 {row[2]}–{row[3]}。")
        result[key] = number
    return timer_config(result)


def settings_checked(data, rules, timer):
    return (room_rules(data.get("config", {}), rules), room_timer(data.get("timer", {}), timer))


def customization_options(rules, timer):
    presets = []
    for path in sorted((ROOT / "presets").glob("*.json")):
        presets.append(dict(name=path.stem, config=room_rules(
            json.loads(path.read_text(encoding="utf-8-sig")), rules)))
    return dict(defaults=dict(config=room_rules({}, rules), timer=copy.deepcopy(timer)), presets=presets,
                game_fields=[dict(key=row[0], label=row[1], min=row[3], max=row[4], integer=row[6]) for row in GAME],
                timer_fields=[dict(key=key, label=label, min=low, max=high,
                                   nullable=key in UNLIMITED_TIMER_FIELDS)
                              for key, label, low, high in TIMER_FIELDS])


def load_rules(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Game configuration must be an object")
    settings, weights = data.get("game_settings", {}), data.get("trump_weights", {})
    if not isinstance(settings, dict) or not isinstance(weights, dict):
        raise ValueError("Invalid game_settings / trump_weights")
    return room_rules(data, rules_checked({}))


def load_clock(path, rules):
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Timer configuration must be an object")
        return timer_config(data)
    return {**DEFAULT_TIMER, "settlement_seconds": rules["game_settings"]["result_screen_duration"]}
