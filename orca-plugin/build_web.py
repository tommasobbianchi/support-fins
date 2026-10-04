#!/usr/bin/env python3
"""Bundle the web app for the OrcaSlicer dock panel.

  python3 orca-plugin/build_web.py
    -> orca-plugin/build/web/{app,finworker,stepworker}.js
    -> orca-plugin/build/panel.html   (the whole page, ready to inline into a dock panel)

Duck-types the esbuild invocation in plugins/shared/bundle.py (same version pin,
same env/PATH resolution). Unlike the engine bundle, this app has a bare-specifier
import ("three", "three/addons/*") that index.html normally resolves with an import
map to web/vendor/three -- esbuild has no import maps, so we pass the same two
mappings as --alias.

Bundles (all in orca-plugin/build/web/, gitignored like the other plugins' build/):
  app.js        IIFE bundle of web/app.js (the whole page).
  finworker.js  classic (non-module) bundle of web/finworker.js; the page
                instantiates it from its source string via a Blob URL worker.
  stepworker.js source emitted verbatim (see docstring) + vendored OCCT assets.

panel.html is plain string composition, no templating engine: index.html's body
markup (importmap + module scripts stripped) + style.css inlined + the finworker
bundle as window.__SF_FINWORKER__ + the app.js bundle, self-contained.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
WEB = HERE.parent / "web"
OUT = HERE / "build" / "web"

# same mappings as web/index.html's <script type="importmap"> (esbuild aliases
# append the sub-path after the mapped path, so no trailing slash)
THREE_ALIAS = f"--alias:three={WEB / 'vendor' / 'three' / 'three.module.js'}"
THREE_ADDONS = f"--alias:three/addons={WEB / 'vendor' / 'three' / 'addons'}"

OCCT = WEB / "vendor" / "occt-import-js-0.0.23"
STEP_SRC = WEB / "stepworker.js"


def esbuild_cmd():
    if os.environ.get("ESBUILD"):
        return [os.environ["ESBUILD"]]
    if shutil.which("esbuild"):
        return ["esbuild"]
    return [shutil.which("npx") or "npx", "--yes", "esbuild@0.28"]


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    base = esbuild_cmd() + [
        "--bundle", "--format=iife", "--target=es2022", "--minify",
        "--log-level=warning", THREE_ALIAS, THREE_ADDONS,
    ]
    for entry, outfile in [
        (WEB / "app.js", OUT / "app.js"),
        (WEB / "finworker.js", OUT / "finworker.js"),
    ]:
        subprocess.run(base + [str(entry), f"--outfile={outfile}"], check=True)
        print(f"bundled {outfile} ({outfile.stat().st_size / 1024:.0f} KB)")
    # stepworker.js: emit source + vendored OCCT assets verbatim (see module docstring)
    (OUT / "stepworker.js").write_text(STEP_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    vendor_out = OUT / "vendor" / "occt-import-js-0.0.23"
    if not vendor_out.exists():
        shutil.copytree(OCCT, vendor_out)
    print(f"emitted {OUT / 'stepworker.js'} (source verbatim) + vendored {OCCT.name}/")
    compose_panel()
    return OUT


def _body_markup():
    """The <body> contents of web/index.html without the importmap + module scripts."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S).group(1)
    # drop the ES-module bootstrap script tags (the bundle replaces them)
    body = re.sub(r'<script\b[^>]*type="module"[^>]*></script>', "", body)
    body = re.sub(r'<script\b[^>]*src="\./app\.js"[^>]*></script>', "", body)
    assert 'type="importmap"' not in body, "importmap leaked into the panel body"
    return body.lstrip("\n").rstrip("\n")


def _js_inlined(src):
    """Make a JavaScript source string safe to embed verbatim inside a <script> tag."""
    assert "</script" not in src.lower(), src[:120]
    return src


def compose_panel():
    style = (WEB / "style.css").read_text(encoding="utf-8")
    finworker = _js_inlined((OUT / "finworker.js").read_text(encoding="utf-8"))
    app = _js_inlined((OUT / "app.js").read_text(encoding="utf-8"))
    # the page reads window.__SF_FINWORKER__ (the finworker source string) to make
    # its Blob-URL worker; json.dumps is the escaping boundary, and any literal
    # </script inside it would close the tag early, so forbid it.
    fin = json.dumps(finworker)
    assert "</script" not in fin.lower()
    body = _body_markup()
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Support Fins</title>'
        + "<style>" + style + "</style>"
        + "<script>window.__SF_FINWORKER__ = " + fin + ";</script></head>"
        + "<body>" + body + "<script>" + app + "</script></body></html>"
    )
    out = HERE / "build" / "panel.html"
    out.write_text(page, encoding="utf-8")
    print(f"composed {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    build()
