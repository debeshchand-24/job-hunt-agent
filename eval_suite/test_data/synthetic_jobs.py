# eval_suite/test_data/synthetic_jobs.py
# Synthetic JDs designed to test specific behaviours.
# Each job is a dict matching A2's extracted_skills output format.
# One variable changed per test case — everything else held constant.

BASE_JD = {
    "role": "Senior Product Manager",
    "company": "TestCo",
    "required_experience_years": "7-9",
    "required_skills": ["growth", "SQL", "A/B testing", "retention", "GA4"],
    "preferred_skills": ["fintech", "ML basics"],
    "domain": "edtech",
    "seniority_level": "SPM",
    "role_type": "growth",
    "key_responsibilities": [
        "Own growth metrics end to end",
        "Run A/B experiments at scale",
        "Work with data and engineering teams"
    ],
    "education_required": "BTech/MBA",
    "location": "Bangalore",
    "tools_mentioned": ["GA4", "Mixpanel"],
    "unique_requirements": ""
}

def make_jd(**overrides) -> dict:
    jd = BASE_JD.copy()
    jd.update(overrides)
    return jd

JD_HARDWARE_PM = make_jd(
    role="Product Manager - Hardware",
    company="HardwareCo",
    domain="hardware manufacturing",
    role_type="hardware",
    required_skills=["circuit design", "PCB layout", "firmware"],
    preferred_skills=["supply chain", "NPI"],
    key_responsibilities=[
        "Own hardware product roadmap",
        "Work with electrical engineers",
        "Manage NPI process"
    ],
    unique_requirements=""
)

JD_GROWTH_FINTECH = make_jd(
    role="Senior PM - Growth",
    company="FintechCo",
    domain="fintech",
    role_type="growth",
    required_skills=["growth hacking", "SQL", "A/B testing"],
)

JD_APM_ROLE = make_jd(
    role="Associate Product Manager",
    company="StartupCo",
    seniority_level="APM",
    required_experience_years="0-2",
    required_skills=["basic analytics", "communication", "curiosity"],
    preferred_skills=["internship experience"],
)

JD_SPM_SWEET_SPOT = make_jd(
    role="Senior Product Manager - Growth",
    company="GrowthCo",
    seniority_level="SPM",
    required_experience_years="7-9",
)

JD_EDTECH_SAME_DOMAIN = make_jd(
    role="Senior PM - Learning",
    company="EdTechCo",
    domain="edtech",
    role_type="growth",
)

JD_ECOMMERCE_ADJACENT = make_jd(
    role="Senior PM - Growth",
    company="EcommerceCo",
    domain="ecommerce",
    role_type="growth",
)

JD_NO_MBA_REQUIREMENT = make_jd(
    unique_requirements=""
)

JD_MBA_REQUIRED_MET = make_jd(
    unique_requirements="MBA from Tier-1 institute required"
)

JD_GROWTH_CV_ROUTING = make_jd(
    role_type="growth",
    domain="consumer",
)

JD_AI_CV_ROUTING = make_jd(
    role="Senior PM - AI Products",
    role_type="ai",
    domain="tech",
    required_skills=["LLMs", "agents", "ML basics", "AI product design"],
    preferred_skills=["prompt engineering", "RAG"],
)

JD_PERFECT_MATCH = make_jd(
    role="Group PM - Growth",
    company="EdTechGrowthCo",
    domain="edtech",
    role_type="growth",
    seniority_level="GPM",
    required_experience_years="7-9",
    required_skills=["growth", "SQL", "A/B testing", "retention", "GA4"],
    preferred_skills=["edtech", "consumer platforms"],
    unique_requirements=""
)

JD_DOMAIN_MISMATCH_INFLATED = make_jd(
    role="Senior PM - Fintech Infrastructure",
    company="InfraCo",
    domain="fintech infrastructure",
    role_type="platform",
    required_skills=["payments", "ledger systems", "reconciliation",
                     "transaction processing", "core banking"],
    preferred_skills=["RBI compliance", "NPCI integrations"],
    unique_requirements=""
)

SCRAPER_CASES = [
    ("Programme Manager, Payments", "Bangalore", False,
     "Exclusion keyword: programme manager"),
    ("Senior Software Engineer", "Bangalore", False,
     "Exclusion keyword: software engineer"),
    ("Senior Product Manager — Growth", "Bangalore", True,
     "Primary keyword match, no exclusion"),
    ("Group Product Manager", "Bangalore", True,
     "Exact primary keyword match"),
    ("Product Manager - Hardware Platform", "Bangalore", True,
     "PM keyword present, hardware handled downstream at A3"),
    ("Senior Product Manager", "Mumbai, Maharashtra", False,
     "Location filter: Mumbai not accepted"),
    ("Senior Product Manager", "Remote / Work from Home", True,
     "Remote accepted in location filter"),
]

import json

def wrap_for_match(jd: dict, job_id: str = "TEST-001") -> dict:
    return {
        "job_id": job_id,
        "role": jd.get("role", "Test Role"),
        "company": jd.get("company", "TestCo"),
        "url": "https://test.com/job",
        "extracted_skills": json.dumps(jd),
        "status": "extracted"
    }

def wrap_for_customise(jd: dict, match_score: int = 85,
                       match_tier: str = "strong",
                       cv_version: str = "growth",
                       job_id: str = "TEST-001") -> dict:
    """
    Wraps a synthetic JD into the sheet row format
    that generate_suggestions() expects.
    Includes match_score and match_tier unlike wrap_for_match.
    """
    return {
        "job_id": job_id,
        "role": jd.get("role", "Test Role"),
        "company": jd.get("company", "TestCo"),
        "url": "https://test.com/job",
        "extracted_skills": json.dumps(jd),
        "match_score": match_score,
        "match_tier": match_tier,
        "cv_version": cv_version,
        "status": "matched"
    }
