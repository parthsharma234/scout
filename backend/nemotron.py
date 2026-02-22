import os
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from dotenv import load_dotenv
load_dotenv(dotenv_path="../.env") # load from root

NEMOTRON_MODEL = "nvidia/llama-3.1-nemotron-70b-instruct" # Standard OpenRouter Nemotron model
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SYSTEM_PROMPT = """
You are an expert VC Analyst AI. Your job is to extract structured data about early-stage startups from internet posts.
You are specifically looking for niche, pre-funding startups gaining organic buzz.

Analyze the provided post text and engagement metrics.
Return a STRICT JSON object with no markdown formatting, no preambles, and no trailing characters.

The JSON MUST conform to this exact structure:
{
  "startup_name": "string",
  "one_liner": "what it does in one sentence",
  "vertical": "one of [AI/ML, Fintech, Health Tech, Climate Tech, B2B SaaS, Consumer, Dev Tools, Web3, Edtech, Other]",
  "business_model": "one of [B2B, B2C, B2B2C, Unclear]",
  "geography": "where the founder is based if detectable, otherwise null",
  "stage": "one of [Pre-idea, Pre-revenue, Early revenue, Waitlist, Just launched]",
  "team_signals": "one of [Solo founder, Multiple founders, Unclear]",
  "traction_signals": "any numbers mentioned such as users, MRR, waitlist size, otherwise null",
  "is_startup": boolean, 
  "is_startup_reason": "one sentence explaining why",
  "scout_score": integer 1-100
}

RULES FOR is_startup:
You must distinguish between actual fundable startups making products vs open source projects, hobby weekend projects, standard discussion threads, newsletters, books, docuseries, courses, or agencies.
Set to `true` ONLY if it is a real software, hardware, or biotech company/product attempting to build a scalable business. Reject media projects, content, and services businesses.

RULES FOR scout_score:
Calculate a score from 1-100 evaluating how interesting this is to an early stage VC.
CRITICAL SCORING INSTRUCTIONS: Do not cluster scores around common numbers like 20, 42, 60, or 80. Use the FULL continuous range from 1 to 100.
Factors:
1. High organic buzz early on (e.g. lots of upvotes/comments in few hours) = Higher score.
2. Low buzz or old post = Lower score.
3. Niche, highly technical, or strong team signals = Higher score.
4. Scale your score smoothly and dynamically based on the exact numbers of upvotes and comments provided. Avoid generic rounded numbers.
"""

def evaluate_post(post_text: str, source: str, engagement: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Sends a scraped post to Nemotron to extract structure and score it.
    Returns the parsed JSON, or None if extraction failed.
    """
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY is not set.")
        return None

    # Construct user message with context
    hours = engagement.get('hours_since_posted', 0)
    metrics_str = ", ".join(f"{k}: {v}" for k, v in engagement.items())
    
    user_prompt = f"""
Source Platform: {source}
Engagement Metrics: {metrics_str}

Post Content:
{post_text}
"""

    payload = {
        "model": NEMOTRON_MODEL,
        "temperature": 0.3, # Allow slight variance to avoid clustering
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": user_prompt.strip()}
        ]
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://scout-local.dev",
            "X-Title": "Scout VC Platform"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
            content = raw["choices"][0]["message"]["content"]
            
            # Clean up potential markdown formatting that sometimes escapes structured output
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
                
            parsed = json.loads(content.strip())
            return parsed
            
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Nemotron API error {e.code}: {body}")
        return None
    except Exception as e:
        print(f"Failed to process with Nemotron: {e}")
        return None

# Quick test
if __name__ == "__main__":
    dummy_text = "Show HN: Lumo - An AI coding agent. Hit 100 stars on github yesterday and we just crossed $5k MRR! Solo founder building out of SF."
    res = evaluate_post(dummy_text, "Hacker News", {"upvotes": 250, "hours_since_posted": 4})
    print(json.dumps(res, indent=2))
