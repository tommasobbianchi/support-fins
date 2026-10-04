"""Build the single-file OrcaSlicer plugin: python3 orca-plugin/build.py

Writes dist/support_fins_any.py -- support_fins.py with the composed dock-panel
page embedded -- which is what OrcaSlicer's plugin hub ships ("_any" = every OS).

Placeholder mechanism: support_fins.py carries the line

    EMBEDDED_PANEL_HTML_GZ_B64 = ""

and _panel_html() unpacks it (gzip + base64) at runtime, so the artifact needs
no build/ dir beside it. This script fills that one line with panel.html
(orca-plugin/build_web.py's output) gzipped to the smallest payload (the app.js
bundle is minified, so gzip buys a lot) and base64-encoded, then drops the rest
of support_fins.py in verbatim. Editing support_fins.py or web/ and re-running
this script is the whole build.
"""
import base64, gzip, os, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))

# bundle the app + compose panel.html before assembly
subprocess.run([sys.executable, os.path.join(here, "build_web.py")], check=True)
panel = open(os.path.join(here, "build", "panel.html"), "rb").read()
payload = base64.b64encode(gzip.compress(panel, 9)).decode()

src = open(os.path.join(here, "support_fins.py")).read()
assert src.count('\nEMBEDDED_PANEL_HTML_GZ_B64 = ""\n') == 1, "placeholder line not found"
out = src.replace('\nEMBEDDED_PANEL_HTML_GZ_B64 = ""\n', '\nEMBEDDED_PANEL_HTML_GZ_B64 = "%s"\n' % payload)
os.makedirs(os.path.join(here, "..", "dist"), exist_ok=True)
path = os.path.join(here, "..", "dist", "support_fins_any.py")
open(path, "w").write(out)
print("%s  %d bytes (panel: %d gz -> %d b64)" % (os.path.normpath(path), len(out), len(gzip.compress(panel, 9)), len(payload)))
