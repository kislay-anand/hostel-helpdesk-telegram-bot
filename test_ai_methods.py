from app.ai_methods import keyword_baseline, rule_based_intent, explain_intent, evaluate_methods


def test_rule_engine_handles_paraphrases():
    assert rule_based_intent("I cannot get online") == "wifi"
    assert rule_based_intent("the tap keeps leaking") == "plumbing"
    assert rule_based_intent("fan stopped running") == "electrical"


def test_unknown_query_falls_back():
    trace = explain_intent("what are the hostel office hours")
    assert trace["intent"] == "unknown"
    assert trace["matched_rule"] is None


def test_keyword_baseline_is_present():
    assert keyword_baseline("wifi is down") == "wifi"


def test_evaluation_returns_both_approaches():
    results = evaluate_methods()
    assert "keyword_baseline" in results
    assert "regex_rule_engine" in results
    assert results["regex_rule_engine"]["total"] > 0
