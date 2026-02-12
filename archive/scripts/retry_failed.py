#!/usr/bin/env python3
"""Retry failed pages with improved JSON extraction."""

import os
import json
import re
import time
import base64
import fitz
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API_KEY = "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"
PDF_PATH = "/root/schar-ocr-v3/source.pdf"
PROMPT_PATH = "/root/schar-ocr-v3/prompt.txt"
OUTPUT_DIR = Path("/root/schar-ocr-v3/pages")
IMAGES_DIR = Path("/root/schar-ocr-v3/images")

def load_prompt():
    with open(PROMPT_PATH) as f:
        return f.read().strip()

def extract_json_robust(raw):
    if not raw:
        return None
    # Try markdown block
    m = re.search(r'```(?:json)?\s*\n?(.*?)```', raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    # Try direct parse
    try:
        return json.loads(raw)
    except:
        pass
    # Fix trailing commas
    fixed = re.sub(r',(\s*[}\]])', r'\1', raw)
    try:
        return json.loads(fixed)
    except:
        pass
    # Try to find outermost braces
    start = raw.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == '{': depth += 1
            elif raw[i] == '}': depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i+1])
                except:
                    break
    # Last resort: fix truncated JSON
    if raw.strip().startswith('{'):
        # Add closing braces
        attempt = raw.strip()
        for _ in range(10):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError as e:
                if 'Expecting' in str(e) and (',' in str(e) or '}' in str(e) or ']' in str(e)):
                    if attempt.rstrip().endswith(','):
                        attempt = attempt.rstrip()[:-1]
                    attempt += ']}'
                else:
                    break
    return None

def process_page(page_num, system_prompt):
    doc = fitz.open(PDF_PATH)
    page = doc[page_num]
    mat = fitz.Matrix(250/72, 250/72)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    doc.close()

    image_b64 = base64.b64encode(img_bytes).decode("utf-8")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://schar-mitzvah-ocr.local",
        "X-Title": "Schar Mitzvah OCR"
    }

    payload = {
        "model": "google/gemini-3-pro-preview",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": f"This is page {page_num + 1} of the PDF (pages 37-120 of the physical book, so this is book page {page_num + 37}). Extract structured content. Return ONLY valid JSON, no markdown fences."}
            ]}
        ],
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "max_tokens": 16000,
        "temperature": 1.0
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()

            content = ""
            thinking = ""
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                msg_content = msg.get("content", "")
                if isinstance(msg_content, str):
                    content = msg_content
                elif isinstance(msg_content, list):
                    for block in msg_content:
                        if isinstance(block, dict):
                            if block.get("type") == "thinking":
                                thinking = block.get("thinking", "")
                            elif block.get("type") == "text":
                                content += block.get("text", "")

            parsed = extract_json_robust(content)
            result = {
                "page": page_num + 1,
                "book_page": page_num + 37,
                "raw_response": content,
                "thinking": thinking,
                "parsed_json": parsed,
                "usage": data.get("usage", {}),
                "status": "success" if parsed else "json_parse_failed"
            }

            out_path = OUTPUT_DIR / f"page_{page_num + 1:03d}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            status = "OK" if parsed else "FAIL"
            print(f"  [Page {page_num + 1}] {status} (attempt {attempt + 1})")
            if parsed:
                return result
        except Exception as e:
            print(f"  [Page {page_num + 1}] Error attempt {attempt + 1}: {e}")
            time.sleep(3)

    return result

def main():
    # Find failed pages
    failed = []
    for i in range(1, 85):
        path = OUTPUT_DIR / f"page_{i:03d}.json"
        if not path.exists():
            failed.append(i - 1)
            continue
        with open(path) as f:
            d = json.load(f)
        if d.get("status") != "success" or not d.get("parsed_json"):
            failed.append(i - 1)

    if not failed:
        print("All pages already successful!")
        # Also fix page 81 with robust parser
        return

    print(f"Retrying {len(failed)} failed pages: {[f+1 for f in failed]}")
    system_prompt = load_prompt()

    with ThreadPoolExecutor(max_workers=len(failed)) as executor:
        futures = {executor.submit(process_page, p, system_prompt): p for p in failed}
        for future in as_completed(futures):
            future.result()

    # Also re-parse page 81 with robust parser
    for i in range(1, 85):
        path = OUTPUT_DIR / f"page_{i:03d}.json"
        if path.exists():
            with open(path) as f:
                d = json.load(f)
            if not d.get("parsed_json") and d.get("raw_response"):
                parsed = extract_json_robust(d["raw_response"])
                if parsed:
                    d["parsed_json"] = parsed
                    d["status"] = "success"
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
                    print(f"  [Page {i}] Fixed via robust parser")

    # Update combined results
    results = []
    for i in range(1, 85):
        path = OUTPUT_DIR / f"page_{i:03d}.json"
        if path.exists():
            with open(path) as f:
                results.append(json.load(f))

    with open("/root/schar-ocr-v3/all_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    success = sum(1 for r in results if r.get("parsed_json"))
    print(f"\nFinal: {success}/84 pages with valid JSON")

if __name__ == "__main__":
    main()
