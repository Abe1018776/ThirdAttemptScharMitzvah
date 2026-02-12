#!/usr/bin/env python3
"""Build HTML viewer showing PDF page images side-by-side with OCR extraction results."""

import json
import base64
from pathlib import Path

PAGES_DIR = Path("/root/schar-ocr-v3/pages")
IMAGES_DIR = Path("/root/schar-ocr-v3/images")
OUTPUT_PATH = Path("/root/schar-ocr-v3/viewer.html")

def embed_image(page_num):
    img_path = IMAGES_DIR / f"page_{page_num:03d}.png"
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def render_parsed_json(parsed):
    """Render parsed JSON data as formatted HTML."""
    if not parsed:
        return '<div class="error">No parsed JSON available</div>'

    html_parts = []

    # Meta section
    meta = parsed.get("meta", {})
    if meta:
        html_parts.append('<div class="meta-box">')
        html_parts.append(f'<span class="meta-label">Page:</span> {meta.get("page_number", "?")} ({meta.get("page_side", "?")})')
        html_parts.append(f' | <span class="meta-label">Chapter:</span> {meta.get("chapter_context", "?")}')
        cf = meta.get("continues_from", {})
        ct = meta.get("continues_to", {})
        if cf and cf.get("flag"):
            html_parts.append(f' | <span class="continues">Continues from prev</span>')
        if ct and ct.get("flag"):
            html_parts.append(f' | <span class="continues">Continues to next</span>')
        html_parts.append('</div>')

    # Data sections
    data = parsed.get("data", [])
    for item in data:
        item_type = item.get("type", "unknown")

        if item_type == "continuation_fragment":
            html_parts.append('<div class="fragment">')
            html_parts.append(f'<div class="item-type">Continuation Fragment</div>')
            html_parts.append(f'<div class="hebrew-text">{item.get("text", "")}</div>')
            src = item.get("source_ref")
            if src:
                html_parts.append(f'<div class="source-ref">({src})</div>')
            html_parts.append('</div>')

        elif item_type == "chapter_header":
            html_parts.append('<div class="chapter-header">')
            html_parts.append(f'<div class="chapter-number">{item.get("number", "")}</div>')
            subtitle = item.get("subtitle", "")
            if subtitle:
                html_parts.append(f'<div class="chapter-subtitle">{subtitle}</div>')
            html_parts.append('</div>')

        elif item_type == "grouping_header":
            html_parts.append('<div class="grouping-header">')
            title = item.get("title", "")
            if isinstance(title, list):
                title = " ".join(str(t) for t in title)
            html_parts.append(f'{title}')
            html_parts.append('</div>')

        elif item_type == "section":
            html_parts.append('<div class="section">')
            num = item.get("number", "")
            title = item.get("title", "")
            html_parts.append(f'<div class="section-header"><span class="section-num">{num}.</span> {title}</div>')

            paragraphs = item.get("paragraphs", [])
            for para in paragraphs:
                is_makor = para.get("is_makor", False)
                css_class = "makor" if is_makor else "body-para"
                html_parts.append(f'<div class="paragraph {css_class}">')
                if is_makor:
                    html_parts.append('<span class="makor-label">מקור המצוה: </span>')
                text = para.get("text", "")
                html_parts.append(f'<span class="hebrew-text">{text}</span>')
                src = para.get("source_ref")
                if src:
                    html_parts.append(f' <span class="source-ref">({src})</span>')
                html_parts.append('</div>')

            html_parts.append('</div>')

        else:
            html_parts.append(f'<div class="unknown-type">[{item_type}] {json.dumps(item, ensure_ascii=False)[:200]}</div>')

    return "\n".join(html_parts)

def build_html():
    pages_html = []

    for page_num in range(1, 85):
        page_path = PAGES_DIR / f"page_{page_num:03d}.json"
        with open(page_path, encoding="utf-8") as f:
            page_data = json.load(f)

        image_b64 = embed_image(page_num)
        book_page = page_num + 36

        parsed = page_data.get("parsed_json")
        rendered = render_parsed_json(parsed)
        status = page_data.get("status", "unknown")
        status_class = "status-ok" if status == "success" else "status-fail"

        raw_json = json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else page_data.get("raw_response", "No data")

        pages_html.append(f'''
        <div class="page-container" id="page-{page_num}">
            <div class="page-header">
                <h2>Page {page_num} (Book Page {book_page})</h2>
                <span class="{status_class}">{status}</span>
                <button class="toggle-btn" onclick="toggleRaw({page_num})">Show Raw JSON</button>
            </div>
            <div class="side-by-side">
                <div class="pdf-panel">
                    <img src="data:image/png;base64,{image_b64}" alt="Page {page_num}" loading="lazy" />
                </div>
                <div class="ocr-panel">
                    <div class="rendered-view" id="rendered-{page_num}">
                        {rendered}
                    </div>
                    <div class="raw-view" id="raw-{page_num}" style="display:none">
                        <pre>{raw_json}</pre>
                    </div>
                </div>
            </div>
        </div>
        ''')

    nav_links = " ".join(f'<a href="#page-{i}">{i}</a>' for i in range(1, 85))

    html = f'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<title>Schar Mitzvah OCR Viewer - Gemini 3 Pro Preview</title>
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
.page-container {{ padding: 20px 30px; border-bottom: 2px solid #333; }}
.page-header {{
    display: flex; align-items: center; gap: 15px;
    margin-bottom: 15px; padding-bottom: 10px;
    border-bottom: 1px solid #444;
}}
.page-header h2 {{ color: #e94560; font-size: 1.2em; }}
.status-ok {{ color: #4caf50; font-weight: bold; }}
.status-fail {{ color: #f44336; font-weight: bold; }}
.toggle-btn {{
    background: #0f3460; color: #fff; border: 1px solid #53a8b6;
    padding: 5px 12px; border-radius: 4px; cursor: pointer; font-size: 0.85em;
}}
.toggle-btn:hover {{ background: #53a8b6; color: #000; }}
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
    <h1>Schar Mitzvah OCR Viewer</h1>
    <div class="stats">
        Model: Gemini 3 Pro Preview (thinking enabled) | 84 pages | PDF pages 37-120
    </div>
</div>
<div class="nav-bar">
    {nav_links}
</div>
{"".join(pages_html)}
<script>
function toggleRaw(pageNum) {{
    const rendered = document.getElementById('rendered-' + pageNum);
    const raw = document.getElementById('raw-' + pageNum);
    if (raw.style.display === 'none') {{
        raw.style.display = 'block';
        rendered.style.display = 'none';
    }} else {{
        raw.style.display = 'none';
        rendered.style.display = 'block';
    }}
}}
</script>
</body>
</html>'''

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Viewer saved to: {OUTPUT_PATH}")
    print(f"Size: {OUTPUT_PATH.stat().st_size / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    build_html()
