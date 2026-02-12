#!/usr/bin/env python3
"""
Concurrent per-page OCR extraction using Gemini 2.5 Pro Preview via OpenRouter.
Converts each PDF page to an image, sends to Gemini with thinking enabled.
"""

import os
import sys
import json
import time
import base64
import fitz  # PyMuPDF
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

OPENROUTER_API_KEY = "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"
PDF_PATH = "/root/schar-ocr-v3/source.pdf"
PROMPT_PATH = "/root/schar-ocr-v3/prompt.txt"
OUTPUT_DIR = Path("/root/schar-ocr-v3/pages")
IMAGES_DIR = Path("/root/schar-ocr-v3/images")
MAX_WORKERS = 20  # concurrent requests

def load_prompt():
    with open(PROMPT_PATH, "r") as f:
        return f.read().strip()

def pdf_page_to_base64(doc, page_num, dpi=250):
    """Convert a single PDF page to a base64-encoded PNG."""
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    # Also save to disk for the viewer
    img_path = IMAGES_DIR / f"page_{page_num + 1:03d}.png"
    with open(img_path, "wb") as f:
        f.write(img_bytes)
    return base64.b64encode(img_bytes).decode("utf-8"), img_path

def call_gemini(page_num, image_b64, system_prompt):
    """Send a single page to Gemini 2.5 Pro Preview via OpenRouter with thinking."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://schar-mitzvah-ocr.local",
        "X-Title": "Schar Mitzvah OCR"
    }

    payload = {
        "model": "google/gemini-3-pro-preview",
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"This is page {page_num + 1} of the PDF (pages 37-120 of the physical book, so this is book page {page_num + 37}). Please extract the structured content from this page according to the instructions."
                    }
                ]
            }
        ],
        "thinking": {
            "type": "enabled",
            "budget_tokens": 10000
        },
        "max_tokens": 16000,
        "temperature": 1.0  # required for thinking
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1) + 5
                print(f"  [Page {page_num + 1}] Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()

            # Extract the text content (skip thinking blocks)
            content = ""
            thinking = ""
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                msg_content = msg.get("content", "")
                # Content could be string or array
                if isinstance(msg_content, str):
                    content = msg_content
                elif isinstance(msg_content, list):
                    for block in msg_content:
                        if isinstance(block, dict):
                            if block.get("type") == "thinking":
                                thinking = block.get("thinking", "")
                            elif block.get("type") == "text":
                                content += block.get("text", "")
                        elif isinstance(block, str):
                            content += block

            return {
                "page": page_num + 1,
                "book_page": page_num + 37,
                "raw_response": content,
                "thinking": thinking,
                "usage": data.get("usage", {}),
                "status": "success"
            }

        except Exception as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                print(f"  [Page {page_num + 1}] Error: {e}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                return {
                    "page": page_num + 1,
                    "book_page": page_num + 37,
                    "raw_response": "",
                    "thinking": "",
                    "error": str(e),
                    "status": "failed"
                }

def extract_json_from_response(raw):
    """Try to extract JSON from the raw response text."""
    if not raw:
        return None
    # Try to find JSON block in markdown
    if "```json" in raw:
        start = raw.index("```json") + 7
        end = raw.index("```", start)
        raw = raw[start:end].strip()
    elif "```" in raw:
        start = raw.index("```") + 3
        end = raw.index("```", start)
        raw = raw[start:end].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to fix common issues
        raw = raw.strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                # Remove trailing commas
                import re
                fixed = re.sub(r',\s*}', '}', raw)
                fixed = re.sub(r',\s*]', ']', fixed)
                return json.loads(fixed)
            except:
                pass
        return None

def process_page(args):
    """Process a single page: convert to image and send to Gemini."""
    page_num, doc_path, system_prompt = args
    doc = fitz.open(doc_path)
    image_b64, img_path = pdf_page_to_base64(doc, page_num)
    doc.close()

    print(f"  [Page {page_num + 1}/84] Sending to Gemini...")
    result = call_gemini(page_num, image_b64, system_prompt)

    # Parse JSON from response
    parsed = extract_json_from_response(result.get("raw_response", ""))
    result["parsed_json"] = parsed
    if parsed:
        result["status"] = "success"
    elif result["status"] == "success":
        result["status"] = "json_parse_failed"

    # Save individual page result
    out_path = OUTPUT_DIR / f"page_{page_num + 1:03d}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    status_icon = "OK" if parsed else "FAIL"
    print(f"  [Page {page_num + 1}/84] {status_icon}")
    return result

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    system_prompt = load_prompt()
    doc = fitz.open(PDF_PATH)
    total_pages = len(doc)
    doc.close()

    print(f"Starting concurrent OCR extraction of {total_pages} pages...")
    print(f"Using {MAX_WORKERS} concurrent workers")
    print(f"Model: google/gemini-3-pro-preview (thinking enabled)")
    print()

    start_time = time.time()

    tasks = [(i, PDF_PATH, system_prompt) for i in range(total_pages)]

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_page, task): task[0] for task in tasks}
        for future in as_completed(futures):
            page_num = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"  [Page {page_num + 1}] EXCEPTION: {e}")
                results.append({
                    "page": page_num + 1,
                    "book_page": page_num + 37,
                    "status": "exception",
                    "error": str(e)
                })

    # Sort by page number
    results.sort(key=lambda x: x["page"])

    elapsed = time.time() - start_time

    # Summary
    success = sum(1 for r in results if r.get("parsed_json"))
    failed = sum(1 for r in results if r["status"] != "success")
    json_fail = sum(1 for r in results if r["status"] == "json_parse_failed")

    print(f"\n{'='*60}")
    print(f"Extraction complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Success (with JSON): {success}/{total_pages}")
    print(f"  JSON parse failed:   {json_fail}/{total_pages}")
    print(f"  API failures:        {failed}/{total_pages}")
    print(f"{'='*60}")

    # Save combined results
    combined_path = OUTPUT_DIR.parent / "all_results.json"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nCombined results saved to: {combined_path}")

    # Save summary
    summary = {
        "total_pages": total_pages,
        "success": success,
        "json_parse_failed": json_fail,
        "api_failed": failed,
        "elapsed_seconds": elapsed,
        "failed_pages": [r["page"] for r in results if r["status"] != "success"]
    }
    with open(OUTPUT_DIR.parent / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

if __name__ == "__main__":
    main()
