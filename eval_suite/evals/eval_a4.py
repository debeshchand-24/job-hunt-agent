import sys
import re
sys.path.append('.')

from agents.cv_customiser_agent import CVCustomiserAgent
from eval_suite.test_data.synthetic_jobs import wrap_for_customise, JD_PERFECT_MATCH
from eval_suite.judges.llm_judge import llm_judge

agent = CVCustomiserAgent()
results = []

def run_test(test_id, description, eval_type, assertion_fn, job=None):
    print(f"\nRunning A4-T{test_id}: {description}")
    try:
        if job is None:
            job = wrap_for_customise(JD_PERFECT_MATCH)
        suggestions = agent.generate_suggestions(job)
        passed, detail = assertion_fn(suggestions)
    except Exception as e:
        passed = False
        detail = f"Exception: {str(e)}"
        suggestions = ""
    results.append({
        "id": f"A4-T{test_id}",
        "description": description,
        "eval_type": eval_type,
        "passed": passed,
        "detail": detail,
    })
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"  {status} — {detail}")

# ── T1: Character count constraint ────────────────────
# Finds all CURRENT/SUGGESTED pairs and checks each
def check_char_constraints(suggestions: str):
    pattern = r'\*\*CURRENT\s*\((\d+)\s*chars?\):\*\*.*?\*\*SUGGESTED\s*\((\d+)\s*chars?\):\*\*'
    matches = re.findall(pattern, suggestions, re.IGNORECASE)
    if not matches:
        return False, "No CURRENT/SUGGESTED char count pairs found in output"
    violations = []
    for original, rewrite in matches:
        orig_len = int(original)
        new_len = int(rewrite)
        if new_len > orig_len + 10:
            violations.append(
                f"rewrite={new_len} > original={orig_len}+10"
            )
    if violations:
        return False, f"{len(violations)} constraint violations: {violations[0]}"
    return True, f"All {len(matches)} rewrites within char limit"

run_test(1, "All rewrites within original + 10 chars", "heuristic",
    check_char_constraints
)

# ── T2: Verification section present ──────────────────
run_test(2, "Verification section present in output", "deterministic",
    lambda s: (
        "verification" in s.lower() or "verify" in s.lower(),
        "verification section found" if "verification" in s.lower()
        else "verification section MISSING from output"
    )
)

# ── T3: No fabricated experience ──────────────────────
run_test(3, "LLM judge: no fabricated experience in rewrites", "llm_judge",
    lambda s: (
        llm_judge(
            s[:3000],  # first 3000 chars to keep cost low
            "The CV suggestions must not invent or fabricate new "
            "experience, roles, companies, metrics, or achievements "
            "that were not in the original CV. Rewrites should only "
            "reframe or reword existing experience."
        )["passed"],
        "LLM judge evaluated hallucination risk"
    )
)

# ── T4: Output contains header block ──────────────────
run_test(4, "Output contains company and role in header", "deterministic",
    lambda s: (
        "EdTechGrowthCo" in s or "Group PM" in s,
        "header block found" if ("EdTechGrowthCo" in s or "Group PM" in s)
        else "header block MISSING — company/role not found in output"
    )
)

# ── Report ─────────────────────────────────────────────
print("\n" + "="*45)
print("EVAL REPORT — A4 CV Customiser Agent")
print("="*45)
passed_count = sum(1 for r in results if r["passed"])
total = len(results)
print(f"Score: {passed_count}/{total} passed\n")

by_type = {"deterministic": [], "heuristic": [], "llm_judge": []}
for r in results:
    by_type[r["eval_type"]].append(r)

for eval_type, cases in by_type.items():
    if cases:
        t_passed = sum(1 for r in cases if r["passed"])
        print(f"  {eval_type}: {t_passed}/{len(cases)}")

print()
for r in results:
    icon = "✓" if r["passed"] else "✗"
    print(f"{icon} {r['id']} [{r['eval_type']}] {r['description']}")
    if not r["passed"]:
        print(f"     -> {r['detail']}")

print("="*45)

def get_results():
    return results
