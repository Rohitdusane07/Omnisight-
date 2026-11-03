import os
import json
import base64
import logging
from pathlib import Path
from typing import Optional
import httpx
from backend.config import settings
from backend.models import Issue, CodeDiff, MarkerPosition

logger = logging.getLogger("omnisight.ai")

PROMPT_TEMPLATE = """You are OmniSight, an expert web QA and visual design analysis AI.
Analyze the provided website screenshot and DOM diagnostics for visual defects, responsive issues, and UX inconsistencies.

DOM Diagnostics Summary:
{diagnostics_summary}

Page URL: {website_url}
Page Title: {page_title}

Detect issues such as:
1. Overlapping elements or collision
2. Clipped / cut-off text or buttons
3. Text overflow
4. Misaligned layout components or broken grids
5. Broken images or missing icons
6. Visual hierarchy or suspicious spacing
7. Mobile / responsive issues

Return ONLY a valid JSON array of objects with the following format:
[
  {{
    "title": "Short descriptive title",
    "category": "visual" | "responsive" | "accessibility" | "functional" | "performance",
    "severity": "critical" | "high" | "medium" | "low",
    "description": "Clear explanation of what is visually wrong and where",
    "affectedComponent": "Navbar / Hero / Footer / Button etc.",
    "selector": "CSS selector or tag name if identifiable",
    "markerPosition": {{ "x": 50, "y": 25 }},  // Approximate percentage coordinates (0-100) on screenshot
    "confidence": 0.88,
    "suggestedFix": "Actionable instructions to resolve the issue",
    "codeDiff": {{
      "before": "CSS or HTML snippet demonstrating the problem",
      "after": "Corrected CSS or HTML snippet",
      "language": "css" | "tsx" | "html",
      "fileName": "src/components/Example.tsx"
    }}
  }}
]

If no clear visual issues are identified from the screenshot, return an empty array [].
Do NOT wrap the JSON in backticks or markdown, return raw JSON array only.
"""

async def analyze_with_gemini(
    screenshot_path: Path,
    website_url: str,
    page_title: str,
    diagnostics_summary: str,
    api_key: str
) -> list[dict]:
    """Call Google Gemini 1.5 Flash / Pro vision API with image bytes."""
    with open(screenshot_path, "rb") as f:
        img_bytes = f.read()
    b64_img = base64.b64encode(img_bytes).decode("utf-8")

    prompt = PROMPT_TEMPLATE.format(
        diagnostics_summary=diagnostics_summary,
        website_url=website_url,
        page_title=page_title
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": b64_img
                    }
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json"
        }
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(url, json=payload)
        if res.status_code != 200:
            logger.warning(f"Gemini API returned status {res.status_code}: {res.text}")
            return []
        data = res.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            # Clean markdown codeblocks if model wrapped them
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except Exception as e:
            logger.error(f"Failed to parse Gemini response: {e}")
            return []

async def analyze_with_openai(
    screenshot_path: Path,
    website_url: str,
    page_title: str,
    diagnostics_summary: str,
    api_key: str
) -> list[dict]:
    """Call OpenAI GPT-4o vision API with base64 image."""
    with open(screenshot_path, "rb") as f:
        img_bytes = f.read()
    b64_img = base64.b64encode(img_bytes).decode("utf-8")

    prompt = PROMPT_TEMPLATE.format(
        diagnostics_summary=diagnostics_summary,
        website_url=website_url,
        page_title=page_title
    )

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_img}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            logger.warning(f"OpenAI API returned status {res.status_code}: {res.text}")
            return []
        data = res.json()
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "issues" in parsed:
                return parsed["issues"]
            return []
        except Exception as e:
            logger.error(f"Failed to parse OpenAI response: {e}")
            return []

def get_rule_based_fallback_issues(dom_issues: list[dict], console_errors: list[str]) -> list[dict]:
    """
    Genuine rule-based fallback when no external AI vision key is configured.
    Converts measured DOM layout anomalies and browser console failures into issues.
    """
    fallback = []
    
    # Add console errors as confirmed errors
    if console_errors:
        err_sample = console_errors[0]
        fallback.append({
            "title": "Uncaught JavaScript Runtime Error",
            "category": "functional",
            "severity": "critical",
            "description": f"The browser console logged fatal errors during execution: {err_sample[:200]}",
            "affectedComponent": "Application Script Runtime",
            "selector": "window.onerror",
            "markerPosition": {"x": 10, "y": 92},
            "confidence": 1.0,
            "suggestedFix": "Check browser console traces and safeguard against null pointer references or unhandled promise rejections.",
            "codeDiff": {
                "before": "const val = data.property;",
                "after": "const val = data?.property ?? null;",
                "language": "javascript",
                "fileName": "src/App.tsx"
            },
            "issueType": "confirmed_error"
        })

    # Return issues found by in-browser DOM diagnostics
    for di in dom_issues:
        fallback.append(di)

    return fallback

async def detect_visual_issues(
    screenshot_path: Path,
    website_url: str,
    page_title: str,
    dom_issues: list[dict],
    console_errors: list[str]
) -> tuple[list[dict], str]:
    """
    Detects visual & layout issues.
    Returns (raw_issues, analysis_mode_description).
    """
    diagnostics_summary = json.dumps({
        "dom_issues_count": len(dom_issues),
        "console_errors_count": len(console_errors),
        "console_errors_sample": console_errors[:3],
        "dom_issues": dom_issues
    }, indent=2)

    gemini_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    openai_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")

    ai_issues = []
    mode = "Rule-based browser layout measurement (AI vision key not set)"

    if gemini_key:
        try:
            logger.info("Running visual analysis with Gemini Vision...")
            raw = await analyze_with_gemini(screenshot_path, website_url, page_title, diagnostics_summary, gemini_key)
            if raw:
                for item in raw:
                    item["issueType"] = "ai_visual"
                ai_issues.extend(raw)
                mode = "Gemini 1.5 Flash Vision AI"
        except Exception as e:
            logger.error(f"Gemini visual analysis failed: {e}")

    elif openai_key:
        try:
            logger.info("Running visual analysis with OpenAI GPT-4o...")
            raw = await analyze_with_openai(screenshot_path, website_url, page_title, diagnostics_summary, openai_key)
            if raw:
                for item in raw:
                    item["issueType"] = "ai_visual"
                ai_issues.extend(raw)
                mode = "GPT-4o Vision AI"
        except Exception as e:
            logger.error(f"OpenAI visual analysis failed: {e}")

    # Combine AI detected visual issues with measured DOM issues
    combined = []
    
    # 1. Measured DOM layout issues (ground truth from real browser DOM)
    for di in dom_issues:
        combined.append(di)

    # 2. Add AI detected visual issues
    for ai_item in ai_issues:
        # Avoid duplicate titles
        if not any(c.get("title", "").lower() == ai_item.get("title", "").lower() for c in combined):
            combined.append(ai_item)

    # 3. Add console errors if not already present
    if console_errors and not any(c.get("category") == "functional" for c in combined):
        combined.append({
            "title": "Uncaught Console Runtime Error",
            "category": "functional",
            "severity": "critical",
            "description": f"Browser reported runtime exception: {console_errors[0][:180]}",
            "affectedComponent": "Client Scripts",
            "selector": "window",
            "markerPosition": {"x": 15, "y": 90},
            "confidence": 1.0,
            "suggestedFix": "Resolve null references and ensure modules load correctly without throwing exceptions.",
            "codeDiff": {
                "before": "data.map(item => item.name)",
                "after": "(data || []).map(item => item.name)",
                "language": "javascript",
                "fileName": "src/main.tsx"
            },
            "issueType": "confirmed_error"
        })

    return combined, mode
