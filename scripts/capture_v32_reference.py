"""Capture the unmodified, self-contained V3.2 reference for visual comparison."""
from pathlib import Path
from e2e_cdp import Chrome


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / 'docs/design/v3.2/MCChecker_V3.2_Config_Prototype.html'
    output = root / 'docs/evidence/v3.2/parity'
    output.mkdir(parents=True, exist_ok=True)
    routes = {'files': 'files', 'evolution': 'files/0/evolution',
              'parameters': 'files/0/standard/101/parameters',
              'machine': 'files/0/machine/1/parameters',
              'updates': 'files/0/updates', 'records': 'files/0/records',
              'hierarchy': 'files/0/evolution?view=list',
              'export': 'files/0/standard/101/export',
              'history': 'files/0/standard/101/history'}
    browser = Chrome()
    try:
        for width, height in ((1366, 768), (1920, 1080), (768, 1024), (375, 812)):
            browser.viewport(width, height)
            for name, route in routes.items():
                browser.navigate(source.as_uri() + '#/' + route)
                assert browser.wait_for("!!document.querySelector('.page-heading')", timeout=10)
                browser.screenshot(str(output / ('prototype-%s-%s.png' % (name, width))))
    finally:
        browser.close()


if __name__ == '__main__':
    main()
