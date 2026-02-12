#!/usr/bin/env python3
"""
Apply Claude Opus QA corrections to OCR results and build a corrected viewer.

Steps:
1. Read each QA file and original OCR result
2. Apply corrections (corrected_json or text replacements from issues)
3. Save corrected results to claude_corrected/
4. Build a self-contained HTML viewer with base64-embedded images
"""

import json
import os
import base64
import html
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = "/root/schar-ocr-v3"
QA_DIR = os.path.join(BASE_DIR, "claude_qa")
PAGES_DIR = os.path.join(BASE_DIR, "pages")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
OUTPUT_DIR = os.path.join(BASE_DIR, "claude_corrected")
VIEWER_PATH = os.path.join(BASE_DIR, "claude_corrected_viewer.html")

NUM_PAGES = 84

os.makedirs(OUTPUT_DIR, exist_ok=True)


# =============================================================================
# Part A: Apply corrections
# =============================================================================

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def apply_text_replacement_recursive(obj, original_text, corrected_text):
    """Recursively walk a JSON object and replace original_text with corrected_text in all string values."""
    replacements = 0
    if isinstance(obj, dict):
        for key in obj:
            if isinstance(obj[key], str):
                if original_text in obj[key]:
                    obj[key] = obj[key].replace(original_text, corrected_text)
                    replacements += 1
            elif isinstance(obj[key], (dict, list)):
                replacements += apply_text_replacement_recursive(obj[key], original_text, corrected_text)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                if original_text in item:
                    obj[i] = item.replace(original_text, corrected_text)
                    replacements += 1
            elif isinstance(item, (dict, list)):
                replacements += apply_text_replacement_recursive(item, original_text, corrected_text)
    return replacements


def process_page(page_num):
    """Process a single page: apply QA corrections and return metadata."""
    page_str = f"page_{page_num:03d}"
    qa_path = os.path.join(QA_DIR, f"{page_str}.json")
    ocr_path = os.path.join(PAGES_DIR, f"{page_str}.json")
    out_path = os.path.join(OUTPUT_DIR, f"{page_str}.json")

    result = {
        "page": page_num,
        "had_corrections": False,
        "corrections_applied": [],
        "quality": None,
        "summary": None,
    }

    # Load original OCR
    ocr_data = load_json(ocr_path)
    parsed_json = copy.deepcopy(ocr_data.get("parsed_json", {}))
    book_page = ocr_data.get("book_page", None)
    result["book_page"] = book_page

    # Load QA
    if not os.path.exists(qa_path):
        # No QA file, just save original
        save_json(out_path, parsed_json)
        return result

    qa_data = load_json(qa_path)
    result["quality"] = qa_data.get("overall_quality", "unknown")
    result["summary"] = qa_data.get("summary", "")

    corrected_json = qa_data.get("corrected_json", None)

    if corrected_json is not None and corrected_json != "null":
        # Use corrected_json directly
        parsed_json = corrected_json
        result["had_corrections"] = True
        result["corrections_applied"].append("Full corrected_json replacement")
    else:
        # Apply issue-level text corrections
        issues = qa_data.get("issues", [])
        for issue in issues:
            original_text = issue.get("original_text", "")
            corrected_text = issue.get("corrected_text", "")
            if original_text and corrected_text and original_text != corrected_text:
                count = apply_text_replacement_recursive(parsed_json, original_text, corrected_text)
                if count > 0:
                    result["had_corrections"] = True
                    result["corrections_applied"].append({
                        "type": issue.get("type", "unknown"),
                        "severity": issue.get("severity", "unknown"),
                        "location": issue.get("location", ""),
                        "original": original_text,
                        "corrected": corrected_text,
                        "replacements_made": count,
                    })

    save_json(out_path, parsed_json)
    return result


def apply_all_corrections():
    """Apply corrections to all pages concurrently."""
    results = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(process_page, i): i for i in range(1, NUM_PAGES + 1)}
        for future in as_completed(futures):
            page_num = futures[future]
            try:
                results[page_num] = future.result()
            except Exception as e:
                print(f"  ERROR on page {page_num}: {e}")
                results[page_num] = {"page": page_num, "had_corrections": False, "error": str(e)}
    return results


# =============================================================================
# Part B: Build the HTML viewer
# =============================================================================

def load_image_base64(page_num):
    """Load a page image as base64."""
    img_path = os.path.join(IMAGES_DIR, f"page_{page_num:03d}.png")
    if not os.path.exists(img_path):
        return None
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def h(text):
    """HTML-escape text."""
    if text is None:
        return ""
    return html.escape(str(text))


def render_paragraph(para):
    """Render a single paragraph dict to HTML."""
    is_makor = para.get("is_makor", False)
    text = para.get("text", "")
    source_ref = para.get("source_ref", None)

    css_class = "paragraph makor" if is_makor else "paragraph body-para"
    parts = [f'<div class="{css_class}">']
    if is_makor:
        parts.append('<span class="makor-label">מקור המצוה: </span>')
    parts.append(f'<span class="hebrew-text">{h(text)}</span>')
    if source_ref:
        parts.append(f'<br><span class="source-ref">({h(source_ref)})</span>')
    parts.append('</div>')
    return "\n".join(parts)


def render_section(section):
    """Render a section dict to HTML."""
    number = section.get("number", "")
    title = section.get("title", "")
    paragraphs = section.get("paragraphs", [])

    parts = ['<div class="section">']
    parts.append(f'<div class="section-header"><span class="section-num">{h(number)}.</span> {h(title)}</div>')
    for para in paragraphs:
        parts.append(render_paragraph(para))
    parts.append('</div>')
    return "\n".join(parts)


def render_data_item(item):
    """Render a single data item based on its type."""
    item_type = item.get("type", "unknown")

    if item_type == "chapter_header":
        number = item.get("number", "")
        subtitle = item.get("subtitle", "")
        return f'''<div class="chapter-header">
<div class="chapter-number">{h(number)}</div>
<div class="chapter-subtitle">{h(subtitle)}</div>
</div>'''

    elif item_type == "grouping_header":
        title = item.get("title", "")
        return f'<div class="grouping-header">{h(title)}</div>'

    elif item_type == "section":
        return render_section(item)

    elif item_type == "continuation_fragment":
        text = item.get("text", "")
        source_ref = item.get("source_ref", None)
        parts = ['<div class="fragment">']
        parts.append('<div class="item-type">המשך מעמוד קודם</div>')
        parts.append(f'<span class="hebrew-text">{h(text)}</span>')
        if source_ref:
            parts.append(f'<br><span class="source-ref">({h(source_ref)})</span>')
        parts.append('</div>')
        return "\n".join(parts)

    elif item_type == "paragraph":
        return render_paragraph(item)

    else:
        return f'<div class="unknown-type">[{h(item_type)}] {h(json.dumps(item, ensure_ascii=False)[:200])}</div>'


def render_meta(meta):
    """Render the meta section."""
    if not meta:
        return ""
    parts = ['<div class="meta-box">']
    book_title = meta.get("book_title", "")
    page_number = meta.get("page_number", "")
    page_side = meta.get("page_side", "")
    chapter_context = meta.get("chapter_context", "")

    parts.append(f'<span class="meta-label">ספר:</span> {h(book_title)} | ')
    parts.append(f'<span class="meta-label">עמוד:</span> {h(page_number)} ({h(page_side)}) | ')
    parts.append(f'<span class="meta-label">הקשר:</span> {h(chapter_context)}')

    continues_from = meta.get("continues_from", {})
    continues_to = meta.get("continues_to", {})
    if continues_from and continues_from.get("flag"):
        sec = continues_from.get("section_letter", "")
        level = continues_from.get("level", "")
        parts.append(f'<br><span class="continues">← ממשיך מ: סעיף {h(sec)} ({h(level)})</span>')
    if continues_to and continues_to.get("flag"):
        sec = continues_to.get("section_letter", "")
        level = continues_to.get("level", "")
        parts.append(f'<br><span class="continues">→ ממשיך ל: סעיף {h(sec)} ({h(level)})</span>')

    parts.append('</div>')
    return "\n".join(parts)


def render_ocr_panel(parsed_json):
    """Render the full OCR panel for a page."""
    if not parsed_json:
        return '<div class="error">No parsed data available</div>'

    meta = parsed_json.get("meta", {})
    data = parsed_json.get("data", [])

    parts = []
    parts.append(render_meta(meta))
    for item in data:
        parts.append(render_data_item(item))
    return "\n".join(parts)


def build_page_html(page_num, img_b64, parsed_json, correction_info):
    """Build the HTML for one page."""
    had_corrections = correction_info.get("had_corrections", False)
    book_page = correction_info.get("book_page", "?")
    quality = correction_info.get("quality", "unknown")
    corrections_applied = correction_info.get("corrections_applied", [])

    corrected_class = " corrected-page" if had_corrections else ""
    correction_badge = ""
    if had_corrections:
        num_fixes = len(corrections_applied)
        correction_badge = f'<span class="correction-badge">{num_fixes} correction{"s" if num_fixes != 1 else ""} applied</span>'

    quality_class = "status-ok" if quality in ("good", "excellent") else "status-warn" if quality == "fair" else "status-fail"

    # Image panel
    if img_b64:
        img_html = f'<img src="data:image/png;base64,{img_b64}" alt="Page {page_num}">'
    else:
        img_html = f'<div class="error">Image not found for page {page_num}</div>'

    # OCR panel
    ocr_html = render_ocr_panel(parsed_json)

    # Corrections detail (collapsible)
    corrections_detail = ""
    if had_corrections:
        detail_items = []
        for c in corrections_applied:
            if isinstance(c, str):
                detail_items.append(f'<li>{h(c)}</li>')
            else:
                orig = c.get("original", "")
                corr = c.get("corrected", "")
                loc = c.get("location", "")
                detail_items.append(
                    f'<li><strong>{h(loc)}</strong>: '
                    f'<span class="diff-old">{h(orig)}</span> → '
                    f'<span class="diff-new">{h(corr)}</span></li>'
                )
        corrections_detail = f'''
<div class="corrections-detail" id="corrections-{page_num}" style="display:none;">
<ul>{"".join(detail_items)}</ul>
</div>'''

    toggle_corrections_btn = ""
    if had_corrections:
        toggle_corrections_btn = f'<button class="toggle-btn corrections-btn" onclick="toggleCorrections({page_num})">Show Corrections</button>'

    return f'''
<div class="page-container{corrected_class}" id="page-{page_num}">
    <div class="page-header">
        <h2>Page {page_num} (Book Page {book_page})</h2>
        <span class="{quality_class}">{h(quality)}</span>
        {correction_badge}
        {toggle_corrections_btn}
        <button class="toggle-btn" onclick="toggleRaw({page_num})">Show Raw JSON</button>
    </div>
    {corrections_detail}
    <div class="side-by-side">
        <div class="pdf-panel">
            {img_html}
        </div>
        <div class="ocr-panel">
            <div id="rendered-{page_num}">
                {ocr_html}
            </div>
            <div id="raw-{page_num}" class="raw-view" style="display:none;">
                <pre>{h(json.dumps(parsed_json, ensure_ascii=False, indent=2))}</pre>
            </div>
        </div>
    </div>
</div>'''


def build_viewer(correction_results):
    """Build the full HTML viewer."""
    print("Building viewer HTML...")

    # Count stats
    total_pages = NUM_PAGES
    pages_with_corrections = sum(1 for r in correction_results.values() if r.get("had_corrections", False))
    total_corrections = sum(len(r.get("corrections_applied", [])) for r in correction_results.values())

    # Load all images and corrected JSONs concurrently
    print("  Loading images and corrected data concurrently...")
    images = {}
    corrected_data = {}

    def load_image_task(pn):
        return pn, load_image_base64(pn)

    def load_corrected_task(pn):
        path = os.path.join(OUTPUT_DIR, f"page_{pn:03d}.json")
        return pn, load_json(path) if os.path.exists(path) else None

    with ThreadPoolExecutor(max_workers=16) as executor:
        img_futures = [executor.submit(load_image_task, i) for i in range(1, NUM_PAGES + 1)]
        json_futures = [executor.submit(load_corrected_task, i) for i in range(1, NUM_PAGES + 1)]

        for future in as_completed(img_futures + json_futures):
            pn, data = future.result()
            if isinstance(data, str) or data is None:
                # It's an image result (base64 string or None)
                if pn not in images:
                    images[pn] = data
                elif pn not in corrected_data:
                    corrected_data[pn] = data
            elif isinstance(data, dict):
                corrected_data[pn] = data
            else:
                # Disambiguate: if it's a string, it's base64
                if pn not in images:
                    images[pn] = data
                else:
                    corrected_data[pn] = data

    # Actually, the disambiguation above is fragile. Let's just redo cleanly.
    images = {}
    corrected_data = {}

    with ThreadPoolExecutor(max_workers=16) as executor:
        img_futures = {executor.submit(load_image_base64, i): i for i in range(1, NUM_PAGES + 1)}
        for future in as_completed(img_futures):
            pn = img_futures[future]
            images[pn] = future.result()

    with ThreadPoolExecutor(max_workers=16) as executor:
        def _load_corrected(pn):
            path = os.path.join(OUTPUT_DIR, f"page_{pn:03d}.json")
            return load_json(path) if os.path.exists(path) else None
        json_futures = {executor.submit(_load_corrected, i): i for i in range(1, NUM_PAGES + 1)}
        for future in as_completed(json_futures):
            pn = json_futures[future]
            corrected_data[pn] = future.result()

    print("  Generating page HTML...")

    # Build nav bar
    nav_links = []
    for i in range(1, NUM_PAGES + 1):
        has_corr = correction_results.get(i, {}).get("had_corrections", False)
        cls = ' class="nav-corrected"' if has_corr else ""
        nav_links.append(f'<a href="#page-{i}"{cls}>{i}</a>')
    nav_html = " ".join(nav_links)

    # Build page sections
    page_htmls = []
    for i in range(1, NUM_PAGES + 1):
        page_html = build_page_html(
            i,
            images.get(i),
            corrected_data.get(i),
            correction_results.get(i, {"had_corrections": False, "book_page": "?", "quality": "unknown", "corrections_applied": []}),
        )
        page_htmls.append(page_html)
        if i % 10 == 0:
            print(f"    Generated page {i}/{NUM_PAGES}")

    all_pages_html = "\n".join(page_htmls)

    full_html = f'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<title>Claude Opus — Corrected OCR Viewer</title>
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
.header .stats .highlight {{ color: #ffa726; font-weight: bold; }}
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
    color: #ffa726; font-weight: bold; border: 1px solid #ffa726;
}}
.nav-bar a.nav-corrected:hover {{ background: #ffa726; color: #000; }}
.page-container {{ padding: 20px 30px; border-bottom: 2px solid #333; }}
.page-container.corrected-page {{ border-right: 4px solid #ffa726; }}
.page-header {{
    display: flex; align-items: center; gap: 15px;
    margin-bottom: 15px; padding-bottom: 10px;
    border-bottom: 1px solid #444;
    flex-wrap: wrap;
}}
.page-header h2 {{ color: #e94560; font-size: 1.2em; }}
.status-ok {{ color: #4caf50; font-weight: bold; }}
.status-warn {{ color: #ffa726; font-weight: bold; }}
.status-fail {{ color: #f44336; font-weight: bold; }}
.correction-badge {{
    background: #ffa726; color: #000; padding: 3px 10px;
    border-radius: 12px; font-size: 0.8em; font-weight: bold;
}}
.toggle-btn {{
    background: #0f3460; color: #fff; border: 1px solid #53a8b6;
    padding: 5px 12px; border-radius: 4px; cursor: pointer; font-size: 0.85em;
}}
.toggle-btn:hover {{ background: #53a8b6; color: #000; }}
.toggle-btn.corrections-btn {{ border-color: #ffa726; }}
.toggle-btn.corrections-btn:hover {{ background: #ffa726; color: #000; }}
.corrections-detail {{
    background: #2a1a00; border: 1px solid #ffa726; border-radius: 6px;
    padding: 12px 16px; margin-bottom: 15px; font-size: 0.9em;
}}
.corrections-detail ul {{ list-style: none; padding: 0; }}
.corrections-detail li {{ margin: 6px 0; padding: 4px 0; border-bottom: 1px solid #333; }}
.diff-old {{ color: #f44336; text-decoration: line-through; }}
.diff-new {{ color: #4caf50; font-weight: bold; }}
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
    border-right: 3px solid #4caf50;
}}
.section-header {{
    font-weight: bold;
    color: #81c784;
    margin-bottom: 8px;
    font-size: 1.05em;
}}
.section-num {{ color: #e94560; font-size: 1.1em; }}
.paragraph {{
    padding: 8px 12px;
    margin: 6px 0;
    border-radius: 4px;
    line-height: 1.8;
}}
.makor {{
    background: rgba(233, 69, 96, 0.1);
    border-right: 2px solid #e94560;
}}
.body-para {{
    background: rgba(83, 168, 182, 0.05);
}}
.makor-label {{ color: #e94560; font-weight: bold; }}
.hebrew-text {{ font-family: 'David', 'Noto Sans Hebrew', serif; font-size: 1.05em; }}
.source-ref {{ color: #ffa726; font-style: italic; font-size: 0.95em; }}
.fragment {{
    background: rgba(255, 167, 38, 0.1);
    border-right: 3px solid #ffa726;
    padding: 10px;
    margin: 8px 0;
    border-radius: 6px;
}}
.fragment .item-type {{ color: #ffa726; font-weight: bold; font-size: 0.85em; margin-bottom: 5px; }}
.raw-view pre {{
    font-family: 'Courier New', monospace;
    font-size: 0.85em;
    white-space: pre-wrap;
    word-break: break-word;
    direction: ltr;
    text-align: left;
    color: #b0b0b0;
    background: #111;
    padding: 10px;
    border-radius: 6px;
}}
.error {{ color: #f44336; padding: 10px; }}
.unknown-type {{ color: #999; font-size: 0.85em; padding: 5px; }}
</style>
</head>
<body>
<div class="header">
    <h1>Claude Opus -- Corrected OCR Viewer</h1>
    <div class="stats">
        Claude Opus QA-corrected output | {total_pages} pages |
        <span class="highlight">{pages_with_corrections} pages corrected</span> |
        <span class="highlight">{total_corrections} total corrections</span>
    </div>
</div>
<div class="nav-bar">
    {nav_html}
</div>
{all_pages_html}
<script>
function toggleRaw(pageNum) {{
    var rendered = document.getElementById('rendered-' + pageNum);
    var raw = document.getElementById('raw-' + pageNum);
    if (raw.style.display === 'none') {{
        raw.style.display = 'block';
        rendered.style.display = 'none';
    }} else {{
        raw.style.display = 'none';
        rendered.style.display = 'block';
    }}
}}
function toggleCorrections(pageNum) {{
    var el = document.getElementById('corrections-' + pageNum);
    if (el.style.display === 'none') {{
        el.style.display = 'block';
    }} else {{
        el.style.display = 'none';
    }}
}}
</script>
</body>
</html>'''

    print(f"  Writing viewer to {VIEWER_PATH}...")
    with open(VIEWER_PATH, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"  Viewer written ({len(full_html) / 1024 / 1024:.1f} MB)")


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 60)
    print("Claude Opus QA Corrections Pipeline")
    print("=" * 60)

    # Step 1: Apply corrections
    print("\nStep 1: Applying corrections to all pages...")
    correction_results = apply_all_corrections()

    # Print summary
    pages_modified = [p for p, r in sorted(correction_results.items()) if r.get("had_corrections")]
    total_corrections = sum(len(r.get("corrections_applied", [])) for r in correction_results.values())

    print(f"\n  Total pages: {NUM_PAGES}")
    print(f"  Pages with corrections: {len(pages_modified)}")
    print(f"  Total corrections applied: {total_corrections}")

    if pages_modified:
        print(f"\n  Modified pages: {pages_modified}")
        print("\n  Correction details:")
        for p in pages_modified:
            r = correction_results[p]
            print(f"    Page {p}:")
            for c in r["corrections_applied"]:
                if isinstance(c, str):
                    print(f"      - {c}")
                else:
                    print(f"      - [{c['type']}] {c['location']}: \"{c['original'][:50]}\" -> \"{c['corrected'][:50]}\"")

    # Step 2: Build viewer
    print(f"\nStep 2: Building HTML viewer...")
    build_viewer(correction_results)

    print("\nDone!")
    print(f"  Corrected JSONs: {OUTPUT_DIR}/")
    print(f"  Viewer: {VIEWER_PATH}")


if __name__ == "__main__":
    main()
