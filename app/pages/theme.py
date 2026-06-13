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
  if (!window.mcSetTreeNode) {{
    window.mcSetTreeNode = function (btn, expanded) {{
      if (!btn) return;
      var row = btn.closest('.tree-row');
      if (!row) return;
      var kids = row.nextElementSibling;
      if (!kids || !kids.classList.contains('children-wrap')) return;
      kids.style.display = expanded ? 'block' : 'none';
      btn.classList.toggle('is-expanded', expanded);
      btn.classList.toggle('is-collapsed', !expanded);
      btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      var icon = btn.querySelector('.mc-tree-toggle-icon');
      if (icon) {{
        icon.textContent = expanded ? '▾' : '▸';
      }}
    }};
  }}
  if (!window.mcToggleTree) {{
    window.mcToggleTree = function (btn) {{
      var expanded = btn && btn.getAttribute('aria-expanded') === 'true';
      window.mcSetTreeNode(btn, !expanded);
    }};
  }}
  if (!window.mcTreeSetAll) {{
    window.mcTreeSetAll = function (panelId, expanded) {{
      var root = document.getElementById(panelId);
      if (!root) return;
      root.querySelectorAll('.tree-row .mc-tree-toggle').forEach(function (btn) {{
        window.mcSetTreeNode(btn, expanded);
      }});
    }};
  }}
}})();
        """.strip()
    )
