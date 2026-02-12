#!/usr/bin/env python3
"""Concurrent Gemini 3 Pro Preview QA verification of OCR results."""

import os, json, time, base64, re, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API_KEY = "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"
IMAGES_DIR = Path("/root/schar-ocr-v3/images")
PAGES_DIR = Path("/root/schar-ocr-v3/pages")
OUTPUT_DIR = Path("/root/schar-ocr-v3/gemini_qa")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with open("/root/schar-ocr-v3/prompt.txt") as f:
    ORIGINAL_PROMPT = f.read().strip()

QA_SYSTEM = """You are a Quality Assurance reviewer for Hebrew OCR extraction from the sefer שכר מצוה (Sechar Mitzvah).

You will receive:
1. The original scanned page image
2. The OCR extraction result (JSON) that was produced by another AI
3. The original prompt/instructions that were used to produce the OCR

Your job is to carefully compare the image against the OCR result and identify any issues.

For EACH issue found, provide:
- type: "missing_text" | "wrong_text" | "wrong_structure" | "missing_source_ref" | "wrong_source_ref" | "wrong_section_number" | "missing_section" | "column_order_error" | "other"
- severity: "critical" | "major" | "minor"
- location: where in the document (e.g., "section ו, paragraph 2")
- description: what's wrong
- original_text: what the OCR produced (if applicable)
- corrected_text: what it should be (if applicable)

Also provide:
- overall_quality: "excellent" | "good" | "fair" | "poor"
- summary: brief overall assessment

If the OCR result needs corrections, provide a corrected version of the parsed_json.

Return your analysis as JSON:
```json
{
  "page": <number>,
  "book_page": <number>,
  "overall_quality": "excellent|good|fair|poor",
  "summary": "brief assessment",
  "issues": [
    {
      "type": "...",
      "severity": "...",
      "location": "...",
      "description": "...",
      "original_text": "...",
      "corrected_text": "..."
    }
  ],
  "corrected_json": null or { corrected parsed_json if needed }
}
```

Return ONLY valid JSON, no markdown fences."""

def verify_page(page_num):
    # Load image
    img_path = IMAGES_DIR / f"page_{page_num:03d}.png"
    with open(img_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Load OCR result
    ocr_path = PAGES_DIR / f"page_{page_num:03d}.json"
    with open(ocr_path, encoding="utf-8") as f:
        ocr_data = json.load(f)

    parsed = ocr_data.get("parsed_json", {})
    ocr_json_str = json.dumps(parsed, ensure_ascii=False, indent=2)

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://schar-mitzvah-ocr.local",
        "X-Title": "Schar Mitzvah QA"
    }

    payload = {
        "model": "google/gemini-3-pro-preview",
        "messages": [
            {"role": "system", "content": QA_SYSTEM},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": f"""Page {page_num} of the PDF (book page {page_num + 36}).

ORIGINAL PROMPT USED FOR OCR:
{ORIGINAL_PROMPT}

OCR RESULT TO VERIFY:
{ocr_json_str}

Please carefully compare the image against the OCR result. Check:
1. Is all text captured? (Right column first, then left column)
2. Are cross-column joins correct?
3. Are section numbers sequential and correct?
4. Are source references correctly extracted?
5. Are grouping headers and chapter headers identified correctly?
6. Is the מקור המצוה label properly handled?
7. Are continuation fragments correct?

Provide your analysis as JSON."""}
            ]}
        ],
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "max_tokens": 16000,
        "temperature": 1.0
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=240)
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()

            content = ""
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                mc = msg.get("content", "")
                if isinstance(mc, str):
                    content = mc
                elif isinstance(mc, list):
                    for block in mc:
                        if isinstance(block, dict) and block.get("type") == "text":
                            content += block.get("text", "")

            # Parse JSON
            parsed_qa = None
            m = re.search(r'```(?:json)?\s*\n?(.*?)```', content, re.DOTALL)
            text_to_parse = m.group(1).strip() if m else content
            try:
                parsed_qa = json.loads(text_to_parse)
            except:
                fixed = re.sub(r',(\s*[}\]])', r'\1', text_to_parse)
                try:
                    parsed_qa = json.loads(fixed)
                except:
                    start = text_to_parse.find('{')
                    if start >= 0:
                        depth = 0
                        for i in range(start, len(text_to_parse)):
                            if text_to_parse[i] == '{': depth += 1
                            elif text_to_parse[i] == '}': depth -= 1
                            if depth == 0:
                                try:
                                    parsed_qa = json.loads(text_to_parse[start:i+1])
                                except:
                                    pass
                                break

            result = {
                "page": page_num,
                "book_page": page_num + 36,
                "raw_response": content,
                "parsed_qa": parsed_qa,
                "status": "success" if parsed_qa else "parse_failed"
            }

            out_path = OUTPUT_DIR / f"page_{page_num:03d}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            quality = parsed_qa.get("overall_quality", "?") if parsed_qa else "?"
            issues = len(parsed_qa.get("issues", [])) if parsed_qa else "?"
            print(f"  [Page {page_num}/84] {quality} ({issues} issues)")
            return result

        except Exception as e:
            if attempt < 2:
                print(f"  [Page {page_num}] Error: {e}, retrying...")
                time.sleep(3)
            else:
                result = {"page": page_num, "book_page": page_num + 36, "status": "failed", "error": str(e)}
                out_path = OUTPUT_DIR / f"page_{page_num:03d}.json"
                with open(out_path, "w") as f:
                    json.dump(result, f)
                print(f"  [Page {page_num}/84] FAILED: {e}")
                return result

def main():
    print(f"Starting Gemini QA verification of 84 pages...")
    print(f"Using 20 concurrent workers")
    start = time.time()

    results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(verify_page, i): i for i in range(1, 85)}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda x: x["page"])
    elapsed = time.time() - start

    success = sum(1 for r in results if r.get("parsed_qa"))
    print(f"\nDone in {elapsed:.0f}s. {success}/84 pages verified successfully.")

    with open(OUTPUT_DIR.parent / "gemini_qa_all.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
