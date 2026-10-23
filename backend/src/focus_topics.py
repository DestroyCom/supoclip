"""Optional creator-supplied topics that steer which moments get clipped."""

import re
from typing import Any, List

MAX_FOCUS_TOPICS = 10
MAX_FOCUS_TOPIC_CHARS = 120

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
_TOPIC_SEPARATORS = re.compile(r"[,;\n]+")


def normalize_focus_topics(value: Any) -> List[str]:
    """Return a clean, de-duplicated topic list; empty means "no focus".

    Accepts a list of strings or a single comma/semicolon/newline separated
    string. Anything else (or blank input) yields an empty list so the default
    clip selection is untouched.
    """
    if isinstance(value, str):
        raw_topics = _TOPIC_SEPARATORS.split(value)
    elif isinstance(value, (list, tuple)):
        raw_topics = [item for item in value if isinstance(item, str)]
    else:
        return []

    topics: List[str] = []
    seen = set()
    for raw in raw_topics:
        topic = _CONTROL_CHARS.sub(" ", raw)
        topic = " ".join(topic.split())[:MAX_FOCUS_TOPIC_CHARS].strip()
        key = topic.casefold()
        if topic and key not in seen:
            seen.add(key)
            topics.append(topic)
        if len(topics) >= MAX_FOCUS_TOPICS:
            break
    return topics
