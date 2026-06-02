import sys
sys.path.append('.')

from agents.matching_agent import MatchingAgent
from eval_suite.test_data.synthetic_jobs import (
    wrap_for_match,
    JD_HARDWARE_PM,
    JD_GROWTH_FINTECH,
    JD_APM_ROLE,
    JD_SPM_SWEET_SPOT,
    JD_EDTECH_SAME_DOMAIN,
    JD_ECOMMERCE_ADJACENT,
    JD_NO_MBA_REQUIREMENT,
    JD_MBA_REQUIRED_MET,
    JD_GROWTH_CV_ROUTING,
    JD_AI_CV_ROUTING,
    JD_PERFECT_MATCH,
    JD_DOMAIN_MISMATCH_INFLATED,
)
from eval_suite.judges.llm_judge import llm_judge

agent = MatchingAgent()
results = []

def run_test(test_id, description, eval_type, jd, assertion_fn):
    print(f"\nRunning A3-T{test_id}: {description}")
    try:
        result = agent.match_job(wrap_for_match(jd))
        passed, detail = assertion_fn(result)
    except Exception as e:
        passed = False
        detail = f"Exception: {str(e)}"
        result = {}
    results.append({
        "id": f"A3-T{test_id}",
        "description": description,
        "eval_type": eval_type,
        "passed": passed,
        "detail": detail,
        "output": result
    })
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"  {status} — {detail}")

run_test(1, "Irrelevant job: all 3 core dims below threshold", "deterministic", JD_HARDWARE_PM,
    lambda r: (
        r.get("match_tier") == "irrelevant" and
        r.get("total_score", 99) <= 40 and
        r.get("apply_recommended") == False,
        f"tier={r.get('match_tier')} score={r.get('total_score')} apply={r.get('apply_recommended')}"
    )
)

run_test(2, "Gate must NOT fire when role_type passes", "deterministic", JD_GROWTH_FINTECH,
    lambda r: (
        r.get("match_tier") != "irrelevant",
        f"tier={r.get('match_tier')} gate correctly stayed open"
    )
)

run_test(3, "APM role: 0-2 yrs required, candidate has 10+", "heuristic", JD_APM_ROLE,
    lambda r: (
        r.get("score_breakdown", {}).get("experience", 99) <= 8,
        f"experience_score={r.get('score_breakdown', {}).get('experience')} (expected <=8)"
    )
)

run_test(4, "SPM role: 7-9 yrs required, candidate has 10+", "heuristic", JD_SPM_SWEET_SPOT,
    lambda r: (
        14 <= r.get("score_breakdown", {}).get("experience", 0) <= 20,
        f"experience_score={r.get('score_breakdown', {}).get('experience')} (expected 14-20)"
    )
)

run_test(5, "EdTech role: same domain as candidate", "heuristic", JD_EDTECH_SAME_DOMAIN,
    lambda r: (
        r.get("score_breakdown", {}).get("domain", 0) == 15,
        f"domain_score={r.get('score_breakdown', {}).get('domain')} (expected 15)"
    )
)

run_test(6, "Ecommerce role: adjacent domain to EdTech", "heuristic", JD_ECOMMERCE_ADJACENT,
    lambda r: (
        9 <= r.get("score_breakdown", {}).get("domain", 0) <= 11,
        f"domain_score={r.get('score_breakdown', {}).get('domain')} (expected 9-11)"
    )
)

run_test(7, "No MBA in JD: others must auto-score 10", "deterministic", JD_NO_MBA_REQUIREMENT,
    lambda r: (
        r.get("score_breakdown", {}).get("others", 0) == 10,
        f"others_score={r.get('score_breakdown', {}).get('others')} (expected 10)"
    )
)

run_test(8, "MBA required and candidate has it: others = 10", "deterministic", JD_MBA_REQUIRED_MET,
    lambda r: (
        r.get("score_breakdown", {}).get("others", 0) == 10,
        f"others_score={r.get('score_breakdown', {}).get('others')} (expected 10)"
    )
)

run_test(9, "Growth role: must route to growth_cv", "deterministic", JD_GROWTH_CV_ROUTING,
    lambda r: (
        r.get("which_cv_used") == "growth",
        f"which_cv_used={r.get('which_cv_used')} (expected growth)"
    )
)

run_test(10, "AI PM role: must route to ai_cv", "deterministic", JD_AI_CV_ROUTING,
    lambda r: (
        r.get("which_cv_used") == "ai",
        f"which_cv_used={r.get('which_cv_used')} (expected ai)"
    )
)

run_test(11, "Perfect match: should score 85+", "heuristic", JD_PERFECT_MATCH,
    lambda r: (
        r.get("total_score", 0) >= 85,
        f"total_score={r.get('total_score')} (expected >=85)"
    )
)

run_test(12, "LLM judge: reasoning must cite domain mismatch", "llm_judge", JD_DOMAIN_MISMATCH_INFLATED,
    lambda r: (
        llm_judge(
            r.get("reasoning", ""),
            "The reasoning must mention that the candidate lacks direct domain experience or that domain is a limiting factor."
        )["passed"],
        "LLM judge evaluated reasoning quality"
    )
)

print("\n" + "="*45)
print("EVAL REPORT — A3 Matching Agent")
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
