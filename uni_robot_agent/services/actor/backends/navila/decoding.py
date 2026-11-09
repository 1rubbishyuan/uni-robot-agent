"""Decode one complete native navigation decision without motion fallbacks."""
import math
import re

_FORWARD = re.compile(r"move forward (25|50|75) (?:cm|centimeters?)", re.IGNORECASE)
_TURN = re.compile(r"turn (left|right) (15|30|45) degrees?", re.IGNORECASE)


def decode_action(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 1024:
        raise ValueError("NaVILA must return one nonempty navigation decision")
    # Whitespace and one sentence terminator do not change the command grammar.
    normalized = " ".join(text.strip().split()).removesuffix(".")
    if normalized.lower() == "stop":
        return "stop", {}
    if match := _FORWARD.fullmatch(normalized):
        return "move_forward", {"distance_m": int(match[1]) / 100}
    if match := _TURN.fullmatch(normalized):
        sign = 1 if match[1].lower() == "left" else -1
        return "turn", {"yaw_rad": sign * math.radians(int(match[2]))}
    raise ValueError("NaVILA output must be one supported forward, turn or stop command")
