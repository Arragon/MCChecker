"""Bundle the isolated, dependency-free design demonstration into one HTML file."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "prototype"
parts = {
    "/* STYLES */": "style.css",
    "/* ROADMAP_STYLES */": "roadmap.css",
    "/* MODEL */": "model.js",
    "/* SOURCES */": "sources.js",
    "/* ROADMAP */": "roadmap.js",
    "/* APP */": "app.js",
}
html = (SOURCE / "shell.html").read_text(encoding="utf-8")
for marker, name in parts.items():
    body = (SOURCE / name).read_text(encoding="utf-8")
    if name.endswith(".js") and "</script" in body.lower():
        raise ValueError("Inline script closing tag in " + name)
    if html.count(marker) != 1:
        raise ValueError("Expected one placeholder: " + marker)
    html = html.replace(marker, body)
target = ROOT / "MCChecker_Config_Prototype.html"
target.write_text(html, encoding="utf-8")
print(f"Built {target.name}: {target.stat().st_size:,} bytes")
