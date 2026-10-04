#!/usr/bin/env python3
"""Bundle the web app for the OrcaSlicer dock panel.

  python3 orca-plugin/build_web.py   # -> orca-plugin/build/web/{app,finworker,stepworker}.js

Duck-types the esbuild invocation in plugins/shared/bundle.py (same version pin,
same env/PATH resolution). Unlike the engine bundle, this app has a bare-specifier
import ("three", "three/addons/*") that index.html normally resolves with an import
map to web/vendor/three -- esbuild has no import maps, so we pass the same two
mappings as --alias.

Outputs (all in orca-plugin/build/web/, gitignored like the other plugins' build/):
  app.js        IIFE bundle of web/app.js (the whole page; run it inside a
                document that has index.html's body + style.css inlined).
  finworker.js  classic (non-module) bundle of web/finworker.js. The page
                instantiates it from its bytes:
                  new Worker(URL.createObjectURL(
                    new Blob([FINWORKER_JS], { type: "text/javascript" })))
                (the source uses `type: "module"`; esbuild bundles the module
                graph into one classic script, so the Blob must be a plain
                classic worker and the page must not pass type:"module").
  stepworker.js  NOT a valid bundle -- see below. web/stepworker.js is a CLASSIC
                worker whose first statement is
                  importScripts('./vendor/occt-import-js-0.0.23/occt-import-js.js')
                esbuild cannot rewrite importScripts, so bundling it produces
                `importScripts` (not a real function) plus an unresolved './vendor/...'.
                Orca mode never calls it (the part comes from the plate as STL;
                the file input + STEP/3MF paths live only in web/ui/io.js's file
                handler, which initOrca() hides). This script still emits the
                source verbatim (plus the vendored occt assets under vendor/) so
                a later phase can serve both from the plugin and swap them for a
                Blob:URL worker if STEP import ever needs to work in-panel.
"""
import os
import pathlib
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
    return OUT


if __name__ == "__main__":
    build()
