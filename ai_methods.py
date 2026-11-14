"""Explainable rule-based intent detection plus a simple keyword baseline."""
from __future__ import annotations

import re

KEYWORDS = {
    "wifi": {"wifi", "internet", "network", "router", "connection"},
    "plumbing": {"water", "tap", "pipe", "leak", "toilet", "shower"},
    "electrical": {"light", "fan", "switch", "power", "socket", "electricity"},
    "cleaning": {"clean", "dirty", "garbage", "dust", "washroom", "trash"},
    "furniture": {"chair", "table", "bed", "wardrobe", "furniture", "desk"},
}

RULES = [
    ("wifi", re.compile(r"\b(wi[\s-]?fi|internet|network|router|connection|online|connect(?:ing)?)\b", re.I)),
    ("plumbing", re.compile(r"\b(water|tap|pipe|leak\w*|drip\w*|toilet|shower|faucet)\b", re.I)),
    ("electrical", re.compile(r"\b(light|bulb|fan|switch|power|socket|electric\w*|outage)\b", re.I)),
    ("cleaning", re.compile(r"\b(clean\w*|dirty|garbage|dust|washroom|trash|rubbish|not collected)\b", re.I)),
    ("furniture", re.compile(r"\b(chair|table|bed|wardrobe|furniture|desk|drawer|broken cot)\b", re.I)),
]


def keyword_baseline(text: str) -> str:
    """Baseline: count exact token matches in the small keyword dictionary."""
    tokens = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    scores = {category: len(tokens & words) for category, words in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "unknown"


def explain_intent(text: str) -> dict[str, str | None]:
    """Return the classification and the exact rule/match for explainability."""
    candidate = text or ""
    for category, pattern in RULES:
        match = pattern.search(candidate)
        if match:
            return {
                "intent": category,
                "matched_rule": pattern.pattern,
                "matched_text": match.group(0),
            }
    return {"intent": "unknown", "matched_rule": None, "matched_text": None}


def rule_based_intent(text: str) -> str:
    return str(explain_intent(text)["intent"])


# Synthetic phrases are for method evaluation only, not user traffic or usage metrics.
TEST_CASES = [
    ("wifi is not working", "wifi"),
    ("I cannot get online in my room", "wifi"),
    ("the internet keeps disconnecting", "wifi"),
    ("router connection keeps dropping", "wifi"),
    ("water is dripping below the sink", "plumbing"),
    ("the tap keeps leaking", "plumbing"),
    ("the shower has no water", "plumbing"),
    ("there is a leak near the toilet", "plumbing"),
    ("my room light went out", "electrical"),
    ("the power socket is loose", "electrical"),
    ("the fan is not running", "electrical"),
    ("there is an electricity outage", "electrical"),
    ("trash has not been collected", "cleaning"),
    ("the washroom is dirty", "cleaning"),
    ("there is dust all over the corridor", "cleaning"),
    ("the rubbish bin is full", "cleaning"),
    ("my chair is broken", "furniture"),
    ("the wardrobe door is damaged", "furniture"),
    ("the bed frame is making noise", "furniture"),
    ("the desk drawer is stuck", "furniture"),
    ("what are the hostel office hours", "unknown"),
    ("can I change my room", "unknown"),
    ("what is the menu today", "unknown"),
    ("hello", "unknown"),
]


def evaluate_methods() -> dict:
    output: dict[str, dict] = {}
    for name, detector in (
        ("keyword_baseline", keyword_baseline),
        ("regex_rule_engine", rule_based_intent),
    ):
        correct = sum(detector(text) == expected for text, expected in TEST_CASES)
        total = len(TEST_CASES)
        output[name] = {
            "correct": correct,
            "total": total,
            "accuracy_percent": round(100 * correct / total, 2) if total else 0.0,
        }
    return output
