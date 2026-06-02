import sys
import datetime
sys.path.append('.')

from eval_suite.evals.eval_a1 import get_results as a1_results
from eval_suite.evals.eval_a3 import get_results as a3_results
from eval_suite.evals.eval_a4 import get_results as a4_results

KNOWN_FAILURES = {
    "A3-T8": "Others dim partial scoring — A3 prompt fix pending",
    "A3-T7": "Others dim auto-score broken by prompt tightening — A3 prompt fix pending",
    "A4-T1": "CURRENT/SUGGESTED regex mismatch — A4 prompt fix pending",
    "A4-T2": "Verification section missing — A4 prompt fix pending",
}

def run_all():
    print("Running A1 evals...")
    a1 = a1_results()
    print("Running A3 evals...")
    a3 = a3_results()
    print("Running A4 evals...")
    a4 = a4_results()

    all_results = a1 + a3 + a4

    print("\n" + "="*50)
    print("EVAL REPORT — Job Hunt Agent System")
    print(f"ran: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50 + "\n")

    suites = [
        ("A1 Scraper      ", a1),
        ("A3 Matching     ", a3),
        ("A4 CV Customiser", a4),
    ]

    total_passed = 0
    total_count = 0

    for label, results in suites:
        passed = sum(1 for r in results if r["passed"])
        count = len(results)
        total_passed += passed
        total_count += count
        bar = "█" * passed + "░" * (count - passed)
        pct = int(passed / count * 100) if count else 0
        print(f"{label}  {passed}/{count}  {bar}  {pct}%")

    print(f"\nOVERALL          {total_passed}/{total_count}   "
          f"{int(total_passed/total_count*100)}%\n")

    failures = [r for r in all_results if not r["passed"]]
    if failures:
        print("FAILURES:")
        for r in failures:
            tag = " [known]" if r["id"] in KNOWN_FAILURES else " [NEW]"
            print(f"  ✗ {r['id']} [{r['eval_type']}]{tag} {r['description']}")
            print(f"      → {r['detail']}")
            if r["id"] in KNOWN_FAILURES:
                print(f"      note: {KNOWN_FAILURES[r['id']]}")

    print("\n" + "="*50)

    new_failures = [r for r in failures if r["id"] not in KNOWN_FAILURES]
    if new_failures:
        print(f"⚠️  {len(new_failures)} NEW failure(s) detected — investigate before shipping")
        sys.exit(1)
    else:
        print("✓ No new failures — all failures are known and logged")
        sys.exit(0)

if __name__ == "__main__":
    run_all()
