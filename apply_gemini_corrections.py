#!/usr/bin/env python3
"""
Apply Gemini QA corrections to OCR results and build a corrected viewer.

Reads QA files from gemini_qa/, applies corrections to original OCR from pages/,
saves corrected JSON to gemini_corrected/, and builds a self-contained HTML viewer.
"""

import json
import os
import base64
import html
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = "/root/schar-ocr-v3"
QA_DIR = os.path.join(BASE_DIR, "gemini_qa")
PAGES_DIR = os.path.join(BASE_DIR, "pages")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
OUTPUT_DIR = os.path.join(BASE_DIR, "gemini_corrected")
VIEWER_PATH = os.path.join(BASE_DIR, "gemini_corrected_viewer.html")
NUM_PAGES = 84


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def apply_text_corrections(parsed_json, issues):
    """Apply text corrections from issues to the parsed_json by doing string replacement."""
    result = copy.deepcopy(parsed_json)
    corrections_applied = []

    for issue in issues:
        original = issue.get("original_text")
        corrected = issue.get("corrected_text")
        if not original or not corrected or original == corrected:
            continue

        # Walk through all text fields in the JSON and do replacements
        count = _replace_in_obj(result, original, corrected)
        if count > 0:
            corrections_applied.append({
                "original": original,
                "corrected": corrected,
                "count": count
            })

    return result, corrections_applied


def _replace_in_obj(obj, original, corrected):
    """Recursively replace original with corrected in all string values."""
    count = 0
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(obj[key], str) and original in obj[key]:
                obj[key] = obj[key].replace(original, corrected)
                count += 1
            elif isinstance(obj[key], (dict, list)):
                count += _replace_in_obj(obj[key], original, corrected)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and original in item:
                obj[i] = item.replace(original, corrected)
                count += 1
            elif isinstance(item, (dict, list)):
                count += _replace_in_obj(item, original, corrected)
    return count


def process_page(page_num):
    """Process a single page: apply corrections and return metadata."""
    page_id = f"page_{page_num:03d}"
    qa_path = os.path.join(QA_DIR, f"{page_id}.json")
    pages_path = os.path.join(PAGES_DIR, f"{page_id}.json")
    output_path = os.path.join(OUTPUT_DIR, f"{page_id}.json")

    # Load original OCR
    original = load_json(pages_path)
    parsed_json = original.get("parsed_json")
    if parsed_json is None:
        # No parsed JSON available
        save_json(output_path, {})
        return {
            "page": page_num,
            "modified": False,
            "method": "no_parsed_json",
            "corrections": [],
            "quality": None,
            "issues_count": 0,
            "summary": "No parsed JSON available"
        }

    # Load QA
    qa = load_json(qa_path)
    parsed_qa = qa.get("parsed_qa")

    if parsed_qa is None:
        # QA parsing failed, use original
        save_json(output_path, parsed_json)
        return {
            "page": page_num,
            "modified": False,
            "method": "qa_parse_failed",
            "corrections": [],
            "quality": None,
            "issues_count": 0,
            "summary": "QA parsing failed, using original"
        }

    quality = parsed_qa.get("overall_quality", "unknown")
    qa_summary = parsed_qa.get("summary", "")
    issues = parsed_qa.get("issues", [])
    corrected_json = parsed_qa.get("corrected_json")

    if corrected_json:
        # Use corrected_json directly
        save_json(output_path, corrected_json)
        return {
            "page": page_num,
            "modified": True,
            "method": "corrected_json",
            "corrections": [
                {"original": iss.get("original_text", ""), "corrected": iss.get("corrected_text", "")}
                for iss in issues if iss.get("original_text") and iss.get("corrected_text")
            ],
            "quality": quality,
            "issues_count": len(issues),
            "summary": qa_summary
        }
    elif issues:
        # Apply text corrections from issues
        corrected, corrections_applied = apply_text_corrections(parsed_json, issues)
        modified = len(corrections_applied) > 0
        save_json(output_path, corrected)
        return {
            "page": page_num,
            "modified": modified,
            "method": "text_replacement" if modified else "no_applicable_corrections",
            "corrections": corrections_applied,
            "quality": quality,
            "issues_count": len(issues),
            "summary": qa_summary
        }
    else:
        # No corrections needed
        save_json(output_path, parsed_json)
        return {
            "page": page_num,
            "modified": False,
            "method": "no_corrections_needed",
            "corrections": [],
            "quality": quality,
            "issues_count": 0,
            "summary": qa_summary
        }


def load_image_base64(page_num):
    """Load an image file and return base64 data URI."""
    img_path = os.path.join(IMAGES_DIR, f"page_{page_num:03d}.png")
    with open(img_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{data}"


def escape(text):
    """HTML-escape text."""
    if text is None:
        return ""
    return html.escape(str(text))


def render_ocr_content(parsed_json):
    """Render parsed OCR JSON into HTML content."""
    if not parsed_json:
        return '<div style="color:#f44336;">No OCR data available</div>'

    parts = []

    # Meta
    meta = parsed_json.get("meta", {})
    if meta:
        meta_parts = []
        if meta.get("book_title"):
            meta_parts.append(f'<span class="meta-label">ספר:</span> {escape(meta["book_title"])}')
        if meta.get("page_number"):
            meta_parts.append(f'<span class="meta-label">עמוד:</span> {escape(meta["page_number"])}')
        if meta.get("page_side"):
            meta_parts.append(f'<span class="meta-label">צד:</span> {escape(meta["page_side"])}')
        if meta.get("chapter_context"):
            meta_parts.append(f'<span class="meta-label">פרק:</span> {escape(meta["chapter_context"])}')

        cf = meta.get("continues_from", {})
        ct = meta.get("continues_to", {})
        if cf and cf.get("flag"):
            meta_parts.append('<span class="continues">&#8592; Continues from prev</span>')
        if ct and ct.get("flag"):
            meta_parts.append('<span class="continues">Continues to next &#8594;</span>')

        parts.append(f'<div class="meta-box">{" | ".join(meta_parts)}</div>')

    # Data
    for item in parsed_json.get("data", []):
        item_type = item.get("type", "")

        if item_type == "chapter_header":
            parts.append(f'''<div class="chapter-header">
<div class="chapter-number">{escape(item.get("number", ""))}</div>
<div class="chapter-subtitle">{escape(item.get("subtitle", ""))}</div>
</div>''')

        elif item_type == "grouping_header":
            title = item.get("title", item.get("text", ""))
            parts.append(f'<div class="grouping-header">{escape(title)}</div>')

        elif item_type == "section":
            sec_html = f'<div class="section">'
            sec_num = item.get("number", "")
            sec_title = item.get("title", "")
            sec_html += f'<div class="section-header"><span class="section-num">{escape(sec_num)}.</span> {escape(sec_title)}</div>'

            for para in item.get("paragraphs", []):
                is_makor = para.get("is_makor", False)
                text = para.get("text", "")
                source_ref = para.get("source_ref", "")

                if is_makor:
                    sec_html += f'<div class="paragraph makor">'
                    sec_html += f'<span class="makor-label">מקור המצוה: </span>'
                else:
                    sec_html += f'<div class="paragraph body-para">'

                sec_html += escape(text)

                if source_ref:
                    sec_html += f' <span class="source-ref">({escape(source_ref)})</span>'

                sec_html += '</div>'

            sec_html += '</div>'
            parts.append(sec_html)

        elif item_type in ("continuation_fragment", "continuation_paragraph"):
            text = item.get("text", "")
            source_ref = item.get("source_ref", "")
            p_html = '<div class="continuation-fragment">'
            p_html += '<div class="cont-label">&#8592; המשך מעמוד הקודם (Continuation from previous page)</div>'
            p_html += f'<div class="hebrew-text">{escape(text)}</div>'
            if source_ref:
                p_html += f' <span class="source-ref">({escape(source_ref)})</span>'
            p_html += '</div>'
            parts.append(p_html)

    return "\n".join(parts)


def build_viewer(page_results):
    """Build the self-contained HTML viewer."""
    print("Building viewer HTML...")

    # Count stats
    total = len(page_results)
    modified = sum(1 for r in page_results if r["modified"])
    total_corrections = sum(len(r["corrections"]) for r in page_results)

    # Load all images and corrected JSON in parallel
    image_data = {}
    corrected_data = {}

    def load_image(pn):
        return pn, load_image_base64(pn)

    def load_corrected(pn):
        path = os.path.join(OUTPUT_DIR, f"page_{pn:03d}.json")
        return pn, load_json(path)

    page_nums = [r["page"] for r in page_results]

    with ThreadPoolExecutor(max_workers=16) as executor:
        img_futures = {executor.submit(load_image, pn): pn for pn in page_nums}
        corr_futures = {executor.submit(load_corrected, pn): pn for pn in page_nums}

        for f in as_completed(img_futures):
            pn, data = f.result()
            image_data[pn] = data

        for f in as_completed(corr_futures):
            pn, data = f.result()
            corrected_data[pn] = data

    print(f"Loaded {len(image_data)} images and {len(corrected_data)} corrected JSONs")

    # Build nav bar
    nav_links = []
    for r in page_results:
        pn = r["page"]
        cls = "nav-corrected" if r["modified"] else ""
        nav_links.append(f'<a href="#page-{pn}" class="{cls}">{pn}</a>')
    nav_html = "\n".join(nav_links)

    # Build page sections
    page_sections = []
    for r in page_results:
        pn = r["page"]
        img_uri = image_data[pn]
        cdata = corrected_data[pn]
        ocr_html = render_ocr_content(cdata)

        # Correction badge
        if r["modified"]:
            badge = f'<span class="badge-corrected">CORRECTED ({r["issues_count"]} issues)</span>'
        else:
            badge = f'<span class="badge-original">ORIGINAL</span>'

        quality = r.get("quality") or "N/A"
        quality_cls = "quality-good" if quality in ("good", "excellent") else "quality-warn"

        # Corrections detail
        corrections_detail = ""
        if r["corrections"]:
            corr_items = []
            for c in r["corrections"]:
                orig = escape(c.get("original", ""))
                corr = escape(c.get("corrected", ""))
                corr_items.append(f'<li><del>{orig}</del> &rarr; <ins>{corr}</ins></li>')
            corrections_detail = f'''<div class="corrections-detail" id="corrections-{pn}" style="display:none;">
<h4>Applied Corrections:</h4>
<ul>{"".join(corr_items)}</ul>
</div>'''

        toggle_btn = ""
        if r["corrections"]:
            toggle_btn = f'<button class="toggle-btn" onclick="toggleCorrections({pn})">Show Corrections</button>'

        section = f'''<div class="page-container" id="page-{pn}">
<div class="page-header">
<h2>Page {pn}</h2>
{badge}
<span class="{quality_cls}">Quality: {escape(quality)}</span>
{toggle_btn}
</div>
{corrections_detail}
<div class="side-by-side">
<div class="pdf-panel"><img src="{img_uri}" alt="Page {pn}" loading="lazy"></div>
<div class="ocr-panel">{ocr_html}</div>
</div>
</div>'''
        page_sections.append(section)

    pages_html = "\n".join(page_sections)

    viewer_html = f'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<title>Gemini 3 Pro Preview — Corrected OCR Viewer</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background: #1a1a2e; color: #e0e0e0; }}
.header {{
    background: linear-gradient(135deg, #16213e, #0f3460);
    padding: 20px 30px;
    position: sticky; top: 0; z-index: 100;
    border-bottom: 2px solid #e94560;
}}
.header h1 {{ color: #fff; font-size: 1.5em; margin-bottom: 8px; }}
.header .stats {{ color: #aaa; font-size: 0.9em; }}
.nav-bar {{
    background: #16213e;
    padding: 10px 30px;
    position: sticky; top: 80px; z-index: 99;
    overflow-x: auto; white-space: nowrap;
    border-bottom: 1px solid #333;
}}
.nav-bar a {{
    color: #53a8b6; text-decoration: none; padding: 4px 8px; margin: 2px;
    font-size: 0.85em; border-radius: 4px; display: inline-block;
}}
.nav-bar a:hover {{ background: #0f3460; color: #fff; }}
.nav-bar a.nav-corrected {{
    color: #ffd700;
    font-weight: bold;
    border-bottom: 2px solid #ffd700;
}}
.page-container {{ padding: 20px 30px; border-bottom: 2px solid #333; }}
.page-header {{
    display: flex; align-items: center; gap: 15px;
    margin-bottom: 15px; padding-bottom: 10px;
    border-bottom: 1px solid #444;
    flex-wrap: wrap;
}}
.page-header h2 {{ color: #e94560; font-size: 1.2em; }}
.badge-corrected {{
    background: #ffd700; color: #000; padding: 3px 10px;
    border-radius: 12px; font-size: 0.8em; font-weight: bold;
}}
.badge-original {{
    background: #4caf50; color: #fff; padding: 3px 10px;
    border-radius: 12px; font-size: 0.8em;
}}
.quality-good {{ color: #4caf50; font-size: 0.85em; }}
.quality-warn {{ color: #ffa726; font-size: 0.85em; }}
.toggle-btn {{
    background: #0f3460; color: #fff; border: 1px solid #53a8b6;
    padding: 5px 12px; border-radius: 4px; cursor: pointer; font-size: 0.85em;
}}
.toggle-btn:hover {{ background: #53a8b6; color: #000; }}
.corrections-detail {{
    background: #2a1a3e;
    border: 1px solid #e94560;
    border-radius: 8px;
    padding: 12px 18px;
    margin-bottom: 15px;
    font-size: 0.9em;
    direction: rtl;
}}
.corrections-detail h4 {{ color: #e94560; margin-bottom: 8px; }}
.corrections-detail ul {{ list-style: none; padding: 0; }}
.corrections-detail li {{
    padding: 4px 0;
    border-bottom: 1px solid #333;
    line-height: 1.6;
}}
.corrections-detail del {{
    color: #f44336;
    text-decoration: line-through;
    background: rgba(244, 67, 54, 0.15);
    padding: 1px 4px;
    border-radius: 3px;
}}
.corrections-detail ins {{
    color: #4caf50;
    text-decoration: none;
    background: rgba(76, 175, 80, 0.15);
    padding: 1px 4px;
    border-radius: 3px;
}}
.side-by-side {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    min-height: 400px;
}}
.pdf-panel {{
    background: #222;
    border: 1px solid #444;
    border-radius: 8px;
    overflow: auto;
    max-height: 90vh;
    text-align: center;
}}
.pdf-panel img {{ width: 100%; height: auto; }}
.ocr-panel {{
    background: #1e1e30;
    border: 1px solid #444;
    border-radius: 8px;
    padding: 15px;
    overflow: auto;
    max-height: 90vh;
    direction: rtl;
}}
.meta-box {{
    background: #0f3460;
    padding: 8px 12px;
    border-radius: 6px;
    margin-bottom: 12px;
    font-size: 0.9em;
    direction: rtl;
}}
.meta-label {{ color: #53a8b6; font-weight: bold; }}
.continues {{ color: #ffa726; font-style: italic; }}
.chapter-header {{
    background: linear-gradient(135deg, #4a1942, #1a1a2e);
    padding: 15px;
    border-radius: 8px;
    text-align: center;
    margin: 15px 0;
    border: 1px solid #e94560;
}}
.chapter-number {{ font-size: 1.4em; color: #e94560; font-weight: bold; }}
.chapter-subtitle {{ color: #ccc; margin-top: 5px; }}
.grouping-header {{
    background: #2a2a4a;
    padding: 10px 15px;
    border-radius: 6px;
    text-align: center;
    margin: 12px 0;
    font-weight: bold;
    color: #53a8b6;
    border-right: 3px solid #53a8b6;
}}
.section {{
    background: #252540;
    border-radius: 8px;
    padding: 12px;
    margin: 10px 0;
    border-right: 3px solid #e94560;
}}
.section-header {{
    color: #53a8b6;
    font-weight: bold;
    font-size: 1.05em;
    margin-bottom: 8px;
    padding-bottom: 5px;
    border-bottom: 1px solid #444;
}}
.section-num {{ color: #e94560; font-size: 1.1em; }}
.paragraph {{
    padding: 8px 10px;
    margin: 6px 0;
    line-height: 1.8;
    border-radius: 4px;
}}
.makor {{
    background: #1a3a2e;
    border-right: 3px solid #4caf50;
    padding-right: 12px;
}}
.makor-label {{ color: #e94560; font-weight: bold; }}
.body-para {{
    background: #1e1e30;
}}
.continuation {{
    border-top: 1px dashed #53a8b6;
    padding-top: 10px;
    margin-top: 10px;
}}
.source-ref {{ color: #ffa726; font-style: italic; font-size: 0.95em; }}
.continuation-fragment {{
    background: rgba(255, 167, 38, 0.1);
    border: 1px dashed #ffa726;
    border-right: 3px solid #ffa726;
    padding: 12px;
    margin: 8px 0 15px 0;
    border-radius: 6px;
}}
.cont-label {{
    color: #ffa726; font-weight: bold; font-size: 0.85em; margin-bottom: 8px;
}}
.hebrew-text {{ font-family: 'David', 'Noto Sans Hebrew', serif; line-height: 1.8; }}
</style>
</head>
<body>
<div class="header">
<h1>Gemini 3 Pro Preview -- Corrected OCR Viewer</h1>
<div class="stats">
Total pages: {total} |
Pages corrected: {modified} |
Total corrections applied: {total_corrections}
</div>
</div>
<div class="nav-bar">
{nav_html}
</div>
{pages_html}
<script>
function toggleCorrections(pageNum) {{
    var el = document.getElementById('corrections-' + pageNum);
    if (el) {{
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }}
}}
</script>
</body>
</html>'''

    with open(VIEWER_PATH, "w", encoding="utf-8") as f:
        f.write(viewer_html)

    file_size_mb = os.path.getsize(VIEWER_PATH) / (1024 * 1024)
    print(f"Viewer saved to {VIEWER_PATH} ({file_size_mb:.1f} MB)")


def main():
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Process all pages in parallel
    print(f"Processing {NUM_PAGES} pages...")
    page_results = []

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(process_page, pn): pn for pn in range(1, NUM_PAGES + 1)}
        for f in as_completed(futures):
            result = f.result()
            page_results.append(result)

    # Sort by page number
    page_results.sort(key=lambda r: r["page"])

    # Print summary
    print("\n=== Correction Summary ===")
    modified_count = 0
    total_corrections = 0
    for r in page_results:
        if r["modified"]:
            modified_count += 1
            n_corr = len(r["corrections"])
            total_corrections += n_corr
            print(f"  Page {r['page']:3d}: {r['method']} ({n_corr} corrections, quality: {r['quality']})")
        elif r["method"] not in ("no_corrections_needed",):
            print(f"  Page {r['page']:3d}: {r['method']}")

    print(f"\nTotal pages: {NUM_PAGES}")
    print(f"Pages modified: {modified_count}")
    print(f"Pages unchanged: {NUM_PAGES - modified_count}")
    print(f"Total corrections: {total_corrections}")

    # Build viewer
    build_viewer(page_results)

    # Save correction report
    report_path = os.path.join(OUTPUT_DIR, "_correction_report.json")
    save_json(report_path, {
        "total_pages": NUM_PAGES,
        "modified_pages": modified_count,
        "total_corrections": total_corrections,
        "pages": page_results
    })
    print(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
