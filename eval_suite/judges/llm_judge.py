import json
import anthropic

client = anthropic.Anthropic()

def llm_judge(output: str, criteria: str) -> dict:
    """
    Reusable LLM-as-judge function.
    Uses Haiku — cheap, fast, good enough for binary quality checks.
    
    Args:
        output: the text to evaluate (agent reasoning, CV rewrite, etc.)
        criteria: what good looks like, in plain English
    
    Returns:
        {passed: bool, score: int (0-10), reason: str}
    """
    prompt = f"""You are evaluating an AI system output against a specific criterion.

OUTPUT TO EVALUATE:
{output}

CRITERION:
{criteria}

Respond in JSON only. No preamble, no markdown, no backticks.
{{
  "passed": true or false,
  "score": 0-10,
  "reason": "one sentence explanation"
}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        raw = response.content[0].text
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "passed": False,
            "score": 0,
            "reason": f"Judge returned unparseable response: {response.content[0].text}"
        }
