"""NiceGUI UI 主题与全局资产注入"""

import json
from pathlib import Path

from nicegui import ui

_APPLIED = False


def ensure_theme() -> None:
    global _APPLIED
    if not _APPLIED:
        ui.colors(
            primary="#2563eb",
            secondary="#0f172a",
            accent="#0ea5e9",
            positive="#16a34a",
            negative="#dc2626",
            warning="#f59e0b",
            info="#0ea5e9",
        )
        _APPLIED = True

    css_path = Path(__file__).resolve().parents[1] / "static" / "css" / "style.css"
    try:
        css = css_path.read_text(encoding="utf-8")
    except OSError:
        css = ""

    if not css:
        return

    ui.run_javascript(
        f"""
(function () {{
  if (!document.getElementById('mc-style')) {{
    var s = document.createElement('style');
    s.id = 'mc-style';
    s.textContent = {json.dumps(css)};
    document.head.appendChild(s);
  }}
  if (!window.mct) {{
    window.mct = function (el) {{
      var row = el.closest('.tree-row');
      if (!row) return;
      var kids = row.nextElementSibling;
      if (!kids || !kids.classList.contains('children-wrap')) return;
      if (kids.style.maxHeight === '0px') {{
        kids.style.maxHeight = 'none';
        el.classList.remove('collapsed');
        el.classList.add('expanded');
      }} else {{
        kids.style.maxHeight = '0px';
        el.classList.add('collapsed');
        el.classList.remove('expanded');
      }}
    }};
  }}
}})();
        """.strip()
    )
