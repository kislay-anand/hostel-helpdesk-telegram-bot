"""Run the synthetic test set for the two intent-detection approaches."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai_methods import TEST_CASES, evaluate_methods  # noqa: E402

if __name__ == "__main__":
    print("SYNTHETIC LABELLED TEST SET — NOT REAL USER CONVERSATIONS")
    print(f"Test examples: {len(TEST_CASES)}")
    for name, metrics in evaluate_methods().items():
        print(f"{name:20} {metrics['correct']}/{metrics['total']} correct | {metrics['accuracy_percent']}% accuracy")
