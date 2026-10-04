"""Build the single-file OrcaSlicer plugin: python3 orca-plugin/build.py

Writes dist/support_fins_any.py -- support_fins.py with web/ embedded -- which is
what OrcaSlicer's plugin hub ships ("_any" = every OS).
"""
import base64, io, os, subprocess, sys, zipfile

here = os.path.dirname(os.path.abspath(__file__))
web = os.path.join(here, "..", "web")

# produce the bundled app (app.js / finworker.js / stepworker.js) before
# assembly so a later phase can inline them into the dock panel html.
subprocess.run([sys.executable, os.path.join(here, "build_web.py")], check=True)
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, _, files in os.walk(web):
        for f in sorted(files):
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, web))
src = open(os.path.join(here, "support_fins.py")).read()
assert src.count('\n_WEB_ZIP = ""\n') == 1
out = src.replace('\n_WEB_ZIP = ""\n', '\n_WEB_ZIP = "%s"\n' % base64.b64encode(buf.getvalue()).decode())
os.makedirs(os.path.join(here, "..", "dist"), exist_ok=True)
path = os.path.join(here, "..", "dist", "support_fins_any.py")
open(path, "w").write(out)
print("%s  %d bytes" % (os.path.normpath(path), len(out)))
