"""Small, per-seat patches derived only from an already private projection."""
import copy


def stable_view(view):
    value = copy.deepcopy(view)
    value.pop("reconnect_seconds", None)
    for player in value["players"]:
        player.pop("clock", None)
        player.pop("preparation", None)
    return value


def make_patch(previous, current):
    old, new = stable_view(previous), stable_view(current)
    changes = {key: value for key, value in new.items()
               if key not in {"type", "revision", "players", "events"}
               and (key not in old or old[key] != value)}
    players = []
    for player, before in zip(new["players"], old["players"]):
        fields = {key: value for key, value in player.items()
                  if key != "id" and (key not in before or before[key] != value)}
        if fields:
            players.append({"id": player["id"], **fields})
    events = new.get("events", [])
    last = old.get("events", [])
    last_id = last[-1]["id"] if last else 0
    reset = (old.get("match_id") != new.get("match_id")
             or bool(events and (events[-1]["id"] < last_id
                                 or events[0]["id"] > last_id + 1)))
    return dict(type="patch", base_revision=previous["revision"],
                revision=current["revision"], changes=changes, players=players,
                events=events if reset else [e for e in events if e["id"] > last_id],
                events_reset=reset)
