#!/usr/bin/env python3
"""Build two QA review HTML viewers - one for Claude analysis, one for Gemini analysis."""

import json
import base64
from pathlib import Path

IMAGES_DIR = Path("/root/schar-ocr-v3/images")
PAGES_DIR = Path("/root/schar-ocr-v3/pages")
CLAUDE_QA_DIR = Path("/root/schar-ocr-v3/claude_qa")
GEMINI_QA_DIR = Path("/root/schar-ocr-v3/gemini_qa")

QUALITY_COLORS = {
    "excellent": "#4caf50",
    "good": "#8bc34a",
    "fair": "#ffa726",
    "poor": "#f44336",
}

SEVERITY_COLORS = {
    "critical": "#f44336",
    "major": "#ff9800",
    "minor": "#ffeb3b",
}

def embed_image(page_num):
    img_path = IMAGES_DIR / f"page_{page_num:03d}.png"
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def render_issues(issues):
    if not issues:
        return '<div class="no-issues">No issues found</div>'
    html = []
    for i, issue in enumerate(issues):
        sev = issue.get("severity", "minor")
        sev_color = SEVERITY_COLORS.get(sev, "#999")
        itype = issue.get("type", "other")
        html.append(f'''<div class="issue">
            <div class="issue-header">
                <span class="severity" style="background:{sev_color}">{sev.upper()}</span>
                <span class="issue-type">{itype}</span>
                <span class="issue-location">{issue.get("location", "")}</span>
            </div>
            <div class="issue-desc">{issue.get("description", "")}</div>''')
        orig = issue.get("original_text", "")
        corr = issue.get("corrected_text", "")
        if orig or corr:
            html.append('<div class="issue-diff">')
            if orig:
                html.append(f'<div class="diff-old"><span class="diff-label">OCR:</span> <span class="hebrew-text">{orig}</span></div>')
            if corr:
                html.append(f'<div class="diff-new"><span class="diff-label">Fix:</span> <span class="hebrew-text">{corr}</span></div>')
            html.append('</div>')
        html.append('</div>')
    return "\n".join(html)

def render_ocr_summary(parsed):
    """Brief summary of what OCR extracted."""
    if not parsed:
        return "No OCR data"
    data = parsed.get("data", [])
    parts = []
    for item in data:
        t = item.get("type", "?")
        if t == "chapter_header":
            parts.append(f'<span class="tag tag-chapter">{item.get("number","")}</span>')
        elif t == "grouping_header":
            title = item.get("title", "")
            if isinstance(title, list):
                title = " ".join(str(x) for x in title)
            parts.append(f'<span class="tag tag-group">{title}</span>')
        elif t == "section":
            parts.append(f'<span class="tag tag-section">{item.get("number","")}</span>')
        elif t == "continuation_fragment":
            parts.append('<span class="tag tag-cont">continuation</span>')
    return " ".join(parts)

def build_viewer(qa_dir, source_label, output_path):
    pages_html = []
    stats = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "unknown": 0,
             "total_issues": 0, "critical": 0, "major": 0, "minor": 0}

    for page_num in range(1, 85):
        qa_path = qa_dir / f"page_{page_num:03d}.json"
        qa_data = {}
        if qa_path.exists():
            with open(qa_path, encoding="utf-8") as f:
                qa_data = json.load(f)

        # For Gemini QA, the analysis is in parsed_qa; for Claude QA it's the top level
        if "parsed_qa" in qa_data and qa_data["parsed_qa"]:
            analysis = qa_data["parsed_qa"]
        else:
            analysis = qa_data

        quality = analysis.get("overall_quality", "unknown")
        summary = analysis.get("summary", "No summary available")
        issues = analysis.get("issues", [])
        has_correction = analysis.get("corrected_json") is not None

        # Stats
        stats[quality] = stats.get(quality, 0) + 1
        stats["total_issues"] += len(issues)
        for issue in issues:
            sev = issue.get("severity", "minor")
            stats[sev] = stats.get(sev, 0) + 1

        # Load OCR result for context
        ocr_path = PAGES_DIR / f"page_{page_num:03d}.json"
        ocr_parsed = None
        if ocr_path.exists():
            with open(ocr_path, encoding="utf-8") as f:
                ocr_parsed = json.load(f).get("parsed_json")

        quality_color = QUALITY_COLORS.get(quality, "#999")
        book_page = page_num + 36
        b64 = embed_image(page_num)
        issues_html = render_issues(issues)
        ocr_summary = render_ocr_summary(ocr_parsed)
        correction_badge = '<span class="has-correction">HAS CORRECTION</span>' if has_correction else ''

        pages_html.append(f'''<div class="page-container" id="page-{page_num}">
            <div class="page-header">
                <h2>Page {page_num} <span class="book-page">(Book {book_page})</span></h2>
                <span class="quality-badge" style="background:{quality_color}">{quality.upper()}</span>
                <span class="issue-count">{len(issues)} issues</span>
                {correction_badge}
                <button class="toggle-btn" onclick="toggleImage({page_num})">Toggle Image</button>
            </div>
            <div class="page-body">
                <div class="image-panel" id="img-{page_num}" style="display:none">
                    <img src="data:image/png;base64,{b64}" alt="Page {page_num}" loading="lazy" />
                </div>
                <div class="qa-panel">
                    <div class="summary-box">{summary}</div>
                    <div class="ocr-tags">{ocr_summary}</div>
                    <div class="issues-list">{issues_html}</div>
                </div>
            </div>
        </div>''')

    nav_links = []
    for i in range(1, 85):
        qa_path = qa_dir / f"page_{i:03d}.json"
        qa_data = {}
        if qa_path.exists():
            with open(qa_path, encoding="utf-8") as f:
                qa_data = json.load(f)
        analysis = qa_data.get("parsed_qa", qa_data) if "parsed_qa" in qa_data else qa_data
        if not analysis:
            analysis = qa_data if qa_data else {}
        q = analysis.get("overall_quality", "unknown") if analysis else "unknown"
        c = QUALITY_COLORS.get(q, "#666")
        n_issues = len(analysis.get("issues", []))
        has_crit = any(iss.get("severity") == "critical" for iss in analysis.get("issues", []))
        border = "border:2px solid #f44336;" if has_crit else ""
        nav_links.append(f'<a href="#page-{i}" style="color:{c};{border}" title="{q} - {n_issues} issues">{i}</a>')

    html = f'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<title>QA Review - {source_label}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',Tahoma,sans-serif;background:#0d1117;color:#e0e0e0}}
.header{{background:linear-gradient(135deg,#161b22,#0d1117);padding:20px 30px;position:sticky;top:0;z-index:100;border-bottom:2px solid #58a6ff}}
.header h1{{color:#fff;font-size:1.4em;margin-bottom:8px}}
.stats{{display:flex;gap:15px;flex-wrap:wrap;font-size:.85em;color:#aaa}}
.stat{{padding:4px 10px;border-radius:4px;background:#161b22}}
.stat-label{{color:#58a6ff}}
.nav-bar{{background:#161b22;padding:8px 30px;position:sticky;top:75px;z-index:99;overflow-x:auto;white-space:nowrap;border-bottom:1px solid #333}}
.nav-bar a{{text-decoration:none;padding:3px 6px;margin:1px;font-size:.8em;border-radius:3px;display:inline-block}}
.nav-bar a:hover{{background:#21262d}}
.page-container{{padding:15px 30px;border-bottom:1px solid #21262d}}
.page-header{{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}}
.page-header h2{{color:#58a6ff;font-size:1.1em}}
.book-page{{color:#888;font-weight:normal;font-size:.9em}}
.quality-badge{{padding:3px 10px;border-radius:4px;font-size:.8em;font-weight:bold;color:#000}}
.issue-count{{color:#aaa;font-size:.85em}}
.has-correction{{background:#9c27b0;color:#fff;padding:3px 8px;border-radius:4px;font-size:.75em;font-weight:bold}}
.toggle-btn{{background:#21262d;color:#58a6ff;border:1px solid #30363d;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:.8em}}
.toggle-btn:hover{{background:#30363d}}
.page-body{{display:flex;gap:15px}}
.image-panel{{flex:1;max-height:80vh;overflow:auto;background:#161b22;border-radius:8px;border:1px solid #30363d}}
.image-panel img{{width:100%;height:auto}}
.qa-panel{{flex:1;min-width:0}}
.summary-box{{background:#161b22;padding:10px 15px;border-radius:6px;margin-bottom:10px;border-right:3px solid #58a6ff;font-size:.95em;line-height:1.6}}
.ocr-tags{{margin-bottom:10px;display:flex;flex-wrap:wrap;gap:4px}}
.tag{{padding:2px 8px;border-radius:3px;font-size:.75em}}
.tag-chapter{{background:#4a1942;color:#e94560}}
.tag-group{{background:#1a3a2a;color:#4caf50}}
.tag-section{{background:#1a2a3a;color:#58a6ff}}
.tag-cont{{background:#3a2a1a;color:#ffa726}}
.issues-list{{display:flex;flex-direction:column;gap:8px}}
.issue{{background:#161b22;border-radius:6px;padding:10px;border:1px solid #21262d}}
.issue-header{{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}}
.severity{{padding:2px 8px;border-radius:3px;font-size:.75em;font-weight:bold;color:#000}}
.issue-type{{color:#58a6ff;font-size:.85em}}
.issue-location{{color:#888;font-size:.8em;font-style:italic}}
.issue-desc{{font-size:.9em;line-height:1.5;margin-bottom:6px}}
.issue-diff{{background:#0d1117;border-radius:4px;padding:8px;font-size:.85em}}
.diff-old{{color:#f85149;margin-bottom:4px;direction:rtl}}
.diff-new{{color:#3fb950;direction:rtl}}
.diff-label{{font-weight:bold;font-size:.8em;margin-left:5px}}
.hebrew-text{{font-family:'David','Noto Sans Hebrew',serif;font-size:1em}}
.no-issues{{color:#3fb950;padding:10px;font-style:italic}}
</style>
</head>
<body>
<div class="header">
    <h1>QA Review — {source_label}</h1>
    <div class="stats">
        <div class="stat"><span class="stat-label">Excellent:</span> {stats["excellent"]}</div>
        <div class="stat"><span class="stat-label">Good:</span> {stats["good"]}</div>
        <div class="stat"><span class="stat-label">Fair:</span> {stats["fair"]}</div>
        <div class="stat"><span class="stat-label">Poor:</span> {stats["poor"]}</div>
        <div class="stat"><span class="stat-label">Total Issues:</span> {stats["total_issues"]}</div>
        <div class="stat" style="color:#f44336"><span class="stat-label">Critical:</span> {stats["critical"]}</div>
        <div class="stat" style="color:#ff9800"><span class="stat-label">Major:</span> {stats["major"]}</div>
        <div class="stat" style="color:#ffeb3b"><span class="stat-label">Minor:</span> {stats["minor"]}</div>
    </div>
</div>
<div class="nav-bar">{" ".join(nav_links)}</div>
{"".join(pages_html)}
<script>
function toggleImage(p){{
    var el=document.getElementById('img-'+p);
    el.style.display=el.style.display==='none'?'block':'none';
}}
</script>
</body>
</html>'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    import os
    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"{source_label} viewer: {output_path} ({size_mb:.1f} MB)")
    print(f"  Stats: {stats}")

if __name__ == "__main__":
    build_viewer(CLAUDE_QA_DIR, "Claude Opus QA", "/root/schar-ocr-v3/claude_qa_viewer.html")
    build_viewer(GEMINI_QA_DIR, "Gemini 3 Pro Preview QA", "/root/schar-ocr-v3/gemini_qa_viewer.html")
