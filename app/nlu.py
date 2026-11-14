"""Intent detection with three comparable approaches:

1. RuleNLU   - hand-written regex patterns (fast, precise, brittle to paraphrase/typos)
2. TfidfNLU  - char+word TF-IDF nearest-example matching (handles typos and paraphrase)
3. HybridNLU - safety rules first, then TF-IDF, then rules as a tie-breaker (what the bot runs)

`eval/evaluate.py` compares them on a labelled test set that is NOT part of the training examples.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UNKNOWN = "unknown"


@dataclass
class Prediction:
    intent: str
    confidence: float
    method: str  # rule | tfidf | fallback


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s'?]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- rules
# Order matters: the first matching intent wins. Safety-critical intents come first.
_RULES: list[tuple[str, str]] = [
    ("distress", r"\b(kill(ing)? myself|end (my|it all)|end my life|want(ed)? to die|wanna die|feel(ing)? like dying|suicid\w*|"
                 r"self[- ]?harm\w*|hurt(ing)? myself|no reason to live|can'?t go on|don'?t want to live|not want to live|"
                 r"better off dead|give up on life)\b"),
    ("emergency", r"\b(fire|smoke|gas (is )?leak\w*|leaking gas|ambulance|unconscious|fainted|collapsed|faint|ragg(ing|ed)|harass(ed|ment|ing)|"
                  r"threat(en|ening)|(electric )?shock|emergency|snake|bleeding|seizure|attack(ed)?|not breathing|can'?t breathe|"
                  r"heart attack|overdose)\b|\b(feel(ing)?|am|being) (very |so )?unsafe\b"),
    ("talk_to_human", r"\b(human|real person|agent|staff member|somebody|someone)\b.*\b(talk|speak|connect|chat|want|need|please)\b|"
                      r"\b(talk|speak|connect|chat|transfer)\b.*\b(human|person|warden|admin|staff|someone|somebody|agent)\b|"
                      r"\bhuman (please|agent)\b|\bescalate\b|\bget me (an? |the )?(admin|warden|human|person|staff)\b|"
                      r"\b(don'?t|do not) (want|wish) to (talk|speak|chat) to (a )?bot\b"),
    ("cancel", r"^(cancel|stop|abort|exit|quit|never ?mind|forget it|start over|discard)\b|\bleave it\b"),
    ("check_status", r"\b(status|track|update on|progress)\b.*\b(complaint|ticket|issue|request)\b|"
                     r"\b(complaint|ticket|issue|request)\b.*\b(status|resolved|fixed|solved|open)\b|^check status"),
    ("my_complaints", r"\b(my|all|list|show|view)\b.*\b(complaints|tickets|requests)\b|complaint history"),
    ("file_complaint", r"\b(file|register|raise|lodge|report|new)\b.*\b(complaint|issue|problem)\b|\bcomplain(t)?\b|"
                       r"\b(not working|broken|leak(ing|age)?|fused|clogged|blocked|dirty|damaged|sparking)\b"),
    ("mess_menu", r"\bmenu\b|\bwhat('s| is| are)? (for|being served|cooking)\b.*\b(lunch|dinner|breakfast|today)\b"),
    ("mess_timing", r"\b(timing|timings|time|hours|open|close|closing)\b.*\b(mess|breakfast|lunch|dinner|snacks|canteen)\b|"
                    r"\b(breakfast|lunch|dinner|snacks|mess)\b.*\b(time|timing|timings|hours)\b"),
    ("laundry", r"\b(laundry|dhobi|washing machine|ironing)\b"),
    ("wifi", r"\b(wifi|wi-fi|internet|router|lan port|network)\b"),
    ("warden_contact", r"\b(warden|caretaker|hostel office|security guard)\b.*\b(number|phone|contact|call|reach)\b|"
                       r"\b(contact|number|phone)\b.*\b(warden|caretaker|office)\b"),
    ("hostel_rules", r"\b(curfew|gate clos\w*|visitor|visiting|guests?|outing|night ?out|rules|lock ?up|in ?time)\b"),
    ("fees", r"\b(fee|fees|rent|deposit|charges|bill)\b"),
    ("feedback", r"\b(feedback|suggestion|suggest)\b"),
    ("help", r"^(help|menu|options|commands)$|\b(what can you do|how (does|do) (this|you)|how to use)\b"),
    ("thanks", r"\b(thanks|thank you|thx|appreciated)\b"),
    ("goodbye", r"^(bye|goodbye|good night|see you|ok bye)\b"),
    ("greeting", r"^(hi+|hello+|hey+|yo|namaste|good (morning|evening|afternoon))\b"),
]
_COMPILED = [(i, re.compile(p)) for i, p in _RULES]


class RuleNLU:
    name = "rules"

    def trace(self, text: str) -> list[dict]:
        """Every rule that fires, in priority order (the first one is the winner). Used for explanations."""
        t = normalize(text)
        return [{"intent": i, "matched": m.group(0)} for i, rx in _COMPILED if (m := rx.search(t))]

    def predict(self, text: str) -> Prediction:
        t = normalize(text)
        for intent, rx in _COMPILED:
            if rx.search(t):
                return Prediction(intent, 1.0, "rule")
        return Prediction(UNKNOWN, 0.0, "fallback")


# --------------------------------------------------------------------------- tf-idf
class TfidfNLU:
    name = "tfidf"

    def __init__(self, intents: dict[str, list[str]] | None = None, threshold: float = 0.42):
        if intents is None:
            intents = json.loads((DATA_DIR / "intents.json").read_text(encoding="utf-8"))
        self.threshold = threshold
        self.labels: list[str] = []
        texts: list[str] = []
        for intent, examples in intents.items():
            for ex in examples:
                self.labels.append(intent)
                texts.append(normalize(ex))
        # char n-grams make the matcher tolerant to typos ("wifi pasword", "laundri")
        self.vec = FeatureUnion([
            ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)),
        ])
        self.texts = texts
        self.matrix = self.vec.fit_transform(texts)
        self._labels = np.array(self.labels)

    def scores(self, text: str) -> dict[str, float]:
        v = self.vec.transform([normalize(text)])
        # FeatureUnion concatenates two L2-normalised blocks -> divide by sqrt(2) to keep cosine in [0, 1]
        sims = (self.matrix @ v.T).toarray().ravel() / 2.0
        best: dict[str, float] = {}
        for lab, s in zip(self.labels, sims):
            if s > best.get(lab, 0.0):
                best[lab] = float(s)
        return best

    def neighbours(self, text: str, k: int = 3) -> list[dict]:
        """The k most similar training examples: this is the evidence behind a TF-IDF decision."""
        v = self.vec.transform([normalize(text)])
        sims = (self.matrix @ v.T).toarray().ravel() / 2.0
        return [{"example": self.texts[i], "intent": self.labels[i], "score": round(float(sims[i]), 3)}
                for i in np.argsort(-sims)[:k]]

    def predict(self, text: str) -> Prediction:
        sc = self.scores(text)
        if not sc:
            return Prediction(UNKNOWN, 0.0, "fallback")
        intent, conf = max(sc.items(), key=lambda kv: kv[1])
        if conf < self.threshold or intent == "out_of_scope":
            return Prediction(UNKNOWN, conf, "fallback")
        return Prediction(intent, conf, "tfidf")


# --------------------------------------------------------------------------- hybrid
class HybridNLU:
    """Safety rules > confident TF-IDF > any other rule > fallback."""
    name = "hybrid"
    SAFETY = ("distress", "emergency", "talk_to_human", "cancel")

    def __init__(self, threshold: float = 0.42, intents: dict[str, list[str]] | None = None):
        self.rules = RuleNLU()
        self.tfidf = TfidfNLU(intents=intents, threshold=threshold)
        self.threshold = threshold

    def _decide(self, text: str) -> tuple[Prediction, str]:
        r = self.rules.predict(text)
        if r.intent in self.SAFETY:
            return r, f"a safety rule matched, so it overrides the statistical model ({r.intent})"
        t = self.tfidf.predict(text)
        if t.intent != UNKNOWN and r.intent == UNKNOWN:
            return t, f"no rule matched; the similarity model is confident ({t.confidence:.2f} >= {self.threshold})"
        if t.intent != UNKNOWN and t.intent == r.intent:
            return t, "the rule and the similarity model agree"
        if t.intent != UNKNOWN and t.confidence >= 0.6:
            return t, f"rule said '{r.intent}' but the similarity model is very confident ({t.confidence:.2f} >= 0.60)"
        if r.intent != UNKNOWN:
            return r, f"the similarity model was not confident enough ({t.confidence:.2f}), so the rule decides"
        return (Prediction(UNKNOWN, t.confidence, "fallback"),
                f"no rule matched and the best similarity ({t.confidence:.2f}) is below {self.threshold}, so I ask for clarification")

    def predict(self, text: str) -> Prediction:
        return self._decide(text)[0]

    def explain(self, text: str) -> dict:
        """Everything needed to justify one decision: rule trace, nearest examples, and the decision path."""
        pred, reason = self._decide(text)
        return {"text": text, "intent": pred.intent, "method": pred.method, "confidence": round(pred.confidence, 3),
                "reason": reason, "rules_fired": self.rules.trace(text), "neighbours": self.tfidf.neighbours(text, 3)}


# --------------------------------------------------------------------------- slot helpers
CATEGORIES = {
    "electrical": r"\b(fan|light|bulb|tube ?light|switch|socket|plug|electric\w*|power|short circuit|geyser|ac|a/c|cooler|charging)\b",
    "plumbing": r"\b(tap|leak\w*|water|flush|toilet|drain|basin|shower|pipe|bathroom|washroom|clogged|geyser)\b",
    "cleaning": r"\b(clean\w*|dirty|dust\w*|garbage|sweep\w*|mop\w*|smell|hygiene|pest|cockroach\w*|rats?|mosquito\w*|bugs?)\b",
    "internet": r"\b(wifi|wi-fi|internet|router|network|lan)\b",
    "furniture": r"\b(bed|chair|table|cupboard|almirah|door|window|lock|mattress|drawer|shelf|furniture)\b",
    "mess": r"\b(mess|food|meal|canteen|breakfast|lunch|dinner)\b",
}
_CAT_RX = {k: re.compile(v) for k, v in CATEGORIES.items()}


def guess_category(text: str) -> str | None:
    t = normalize(text)
    best, best_n = None, 0
    for cat, rx in _CAT_RX.items():
        n = len(rx.findall(t))
        if n > best_n:
            best, best_n = cat, n
    return best


_URGENT = re.compile(r"\b(urgent|urgently|asap|immediately|dangerous|sparking|spark|no water|flood\w*|exposed wire|shock)\b")


def looks_urgent(text: str) -> bool:
    return bool(_URGENT.search(normalize(text)))


_ROOM = re.compile(r"\b(?:room\s*(?:no\.?|number|#)?\s*)?([a-z]{0,2}[- ]?\d{1,4}[a-z]?)\b", re.I)


def parse_room(text: str) -> str | None:
    """Accepts '204', 'A-204', 'B 112', 'room 305'. Returns a normalised string or None."""
    t = text.strip()
    if len(t) > 24:
        return None
    m = _ROOM.search(t)
    if not m:
        return None
    room = re.sub(r"\s+", "-", m.group(1).upper().strip())
    return room if any(c.isdigit() for c in room) else None
