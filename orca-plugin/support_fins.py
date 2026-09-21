# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
#
# [tool.orcaslicer.plugin]
# name = "Support Fins"
# description = "Adds breakaway support fins, grip tines and a bed pad to a part as it sits on the plate, baked into the mesh, then loads the finned part back onto the plate. Support Fins is by Matthew Trahan (github.com/gittrahan/support-fins, printfins.com); the designed-in fin technique is Slant 3D's. OrcaSlicer port by Tommaso Bianchi."
# author = "Matthew Trahan (gittrahan), Slant 3D technique; Orca port by Tommaso Bianchi"
# version = "0.1.0"
# ///
"""Support Fins for OrcaSlicer.

Credits: Support Fins -- the app, its fin engine and everything in web/ -- is by
Matthew Trahan (https://github.com/gittrahan/support-fins, MIT). The technique it
automates, breakaway support fins designed into the part, is Slant 3D's. This file
is only the OrcaSlicer port (Tommaso Bianchi).

The fin engine is the browser app in web/ (the same code as printfins.com), run
unchanged inside Orca's plugin webview. The host API is read-only, so:

  1. the plate objects' meshes are read through orca.host.model, with their
     instance/volume transforms applied, so the part arrives as oriented in Orca;
  2. a loopback HTTP server serves web/, those meshes, and the slicer settings the
     fins depend on (layer height -> tine height, filament -> PLA/PETG, bed size);
  3. "Send to OrcaSlicer" POSTs the finned STL back, which is written under the
     data directory and loaded onto the plate the way a second launch would.
"""
import functools
import json
import os
import re
import struct
import subprocess
import sys
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    import orca
except ImportError:          # imported by the offline test
    orca = None

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")

# The OrcaCloud build (build.py) is a single file, so it carries web/ here as a
# base64 zip and unpacks it under the data directory on first run.
_WEB_ZIP = ""


def _log(msg):
    # stderr is teed by the host into <datadir>/log/python_*.log
    print("[support_fins] " + msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ geometry

def stl_bytes(tris, name="support-fins"):
    """Binary STL from an (M, 3, 3) float array. The web app recomputes normals."""
    import numpy as np
    tris = np.ascontiguousarray(tris, dtype="<f4").reshape(-1, 9)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", 9), ("a", "<u2")])
    rec["v"] = tris
    head = name.encode("ascii", "replace")[:79].ljust(80, b" ")
    return head + struct.pack("<I", len(tris)) + rec.tobytes()


def world_triangles(obj, instance=0):
    """All model-part triangles of `obj` in plate coordinates (mm), (M, 3, 3)."""
    import numpy as np
    inst = np.asarray(obj.instance(instance).matrix(), dtype=float)
    out = []
    for vol in obj.volumes():
        if not vol.is_model_part():
            continue          # modifiers, blockers, negative volumes are not geometry
        mesh = vol.mesh()
        v = np.asarray(mesh.vertices(), dtype=float)
        t = np.asarray(mesh.triangles())
        if not len(t):
            continue
        m = inst @ np.asarray(vol.matrix(), dtype=float)
        w = v @ m[:3, :3].T + m[:3, 3]
        tri = w[t]
        if np.linalg.det(m[:3, :3]) < 0:
            tri = tri[:, ::-1]    # a mirror flips the winding; flip it back
        out.append(tri)
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


# ------------------------------------------------------------------ settings

def _cfg(key):
    try:
        v = orca.host.preset_bundle().full_config_value(key)
    except Exception as e:
        _log("config %s unreadable: %r" % (key, e))
        return None
    return v[0] if isinstance(v, (list, tuple)) and v else v


def _nums(v):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(v))]


def slicer_settings():
    """The Orca settings the fin geometry has to agree with, as web-app values."""
    out = {}
    lh = _nums(_cfg("layer_height"))
    if lh:
        out["layer_height"] = lh[0]
    ft = str(_cfg("filament_type") or "").upper()
    # PETG-family filaments weld to supports; everything else takes the PLA profile.
    out["material"] = "petg" if ("PETG" in ft or "PCTG" in ft) else "pla"
    area = _nums(_cfg("printable_area"))        # "0x0,256x0,256x256,0x256"
    height = _nums(_cfg("printable_height"))
    if len(area) >= 4 and height:
        xs, ys = area[0::2], area[1::2]
        out["volume"] = [round(max(xs) - min(xs)), round(max(ys) - min(ys)), round(height[0])]
    return out


# ------------------------------------------------------------------ hand-off

def _orca_data_dir():
    """<datadir> for a plugin installed at <datadir>/orca_plugins/<name>/."""
    path = HERE
    while True:
        parent, leaf = os.path.split(path)
        if leaf == "orca_plugins":
            return parent
        if not parent or parent == path:
            return ""
        path = parent


def _instance_payload(paths):
    """instance_check's argv, unescape_strings_cstyle format; argv[0] is skipped."""
    argv = ["orca-slicer"] + list(paths)
    return ";".join('"%s"' % a.replace("\\", "\\\\").replace('"', '\\"') for a in argv)


def _orca_executable():
    """Absolute path of the running OrcaSlicer binary.

    Each OS has its own way to ask; sys.executable is the embedded interpreter's
    idea of it and is only the last resort.
    """
    if sys.platform == "darwin":
        import ctypes

        buf = ctypes.create_string_buffer(4096)
        size = ctypes.c_uint32(len(buf))
        if ctypes.CDLL(None)._NSGetExecutablePath(buf, ctypes.byref(size)) == 0:
            return os.path.realpath(buf.value.decode())
    elif os.name == "nt":
        import ctypes

        buf = ctypes.create_unicode_buffer(4096)
        if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf)):
            return buf.value
    else:
        try:
            return os.path.realpath("/proc/self/exe")
        except OSError:
            pass
    return sys.executable or ""


def _handoff_windows(paths):
    """Deliver the files through WM_COPYDATA, the way a second launch would.

    GUI_App registers an MSW handler for WM_COPYDATA with dwData == 1 and passes
    the wide string straight to handle_message(). InstanceCheck finds the target
    by comparing instance hashes, but this code runs *inside* the target process,
    so matching our own PID identifies the window without any hash at all.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetPropW.restype = wintypes.HANDLE
    user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SendMessageW.restype = ctypes.c_longlong
    user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p]

    class COPYDATASTRUCT(ctypes.Structure):
        _fields_ = [("dwData", ctypes.c_void_p),
                    ("cbData", wintypes.DWORD),
                    ("lpData", ctypes.c_void_p)]

    our_pid = kernel32.GetCurrentProcessId()
    found = []

    def _enum(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, name, 256) == 0 or name.value != "wxWindowNR":
            return True
        # Both properties are set on the main frame only (init_windows_properties).
        if not user32.GetPropW(hwnd, "Instance_Hash_Minor"):
            return True
        if not user32.GetPropW(hwnd, "Instance_Hash_Major"):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != our_pid:
            return True
        found.append(hwnd)
        return False

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_enum)
    user32.EnumWindows(proc, 0)
    if not found:
        return False, "no OrcaSlicer main window found in this process"

    buf = ctypes.create_unicode_buffer(_instance_payload(paths))
    data = COPYDATASTRUCT(ctypes.c_void_p(1), ctypes.sizeof(buf),
                          ctypes.cast(buf, ctypes.c_void_p))
    # WM_COPYDATA. SendMessage blocks until the GUI thread has handled it, which
    # is fine from this worker thread and would deadlock only on the GUI thread.
    user32.SendMessageW(found[0], 0x004A, None, ctypes.byref(data))
    return True, ""


def _handoff_macos(paths):
    """Hand the files to the running app through LaunchServices.

    `open -a <bundle> <file>` sends an "open documents" Apple Event to the
    instance that is already running, which wxWidgets turns into MacOpenFiles().
    Orca only spawns a second slicer there for .3mf files, and models arrive as
    .stl/.step, so they load into this instance. This avoids reimplementing
    NSDistributedNotificationCenter in ctypes for no gain.
    """
    exe = _orca_executable()
    bundle = exe
    for _ in range(3):  # <bundle>.app/Contents/MacOS/<exe>
        bundle = os.path.dirname(bundle)
    if not bundle.endswith(".app"):
        return False, "not running from an .app bundle (%s)" % exe
    try:
        subprocess.Popen(["/usr/bin/open", "-a", bundle] + list(paths),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, "open -a %s: %s" % (os.path.basename(bundle), e)
    return True, ""


def load_in_orca(path):
    """Put `path` on the running Orca's plate, the way a second launch would.

    Each OS has its own transport to the listener in InstanceCheck.cpp / GUI_App.
    Ported from Orca_plugin_Search_Engine, where only the Linux one ran on hardware.
    """
    if os.name == "nt":
        return _handoff_windows([path])
    if sys.platform == "darwin":
        return _handoff_macos([path])
    return _load_dbus(path)


def _load_dbus(path):
    """Linux: D-Bus AnotherInstance on the session bus."""
    try:
        names = subprocess.run(
            ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus",
             "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"],
            capture_output=True, text=True, timeout=15).stdout
    except Exception as e:
        return False, "dbus-send unavailable: %s" % e
    found = re.findall(r"com\.orcaslicer\.OrcaSlicer\.InstanceCheck\.Object(\d+)", names)
    if not found:
        return False, "no running OrcaSlicer on the session bus"
    # With two Orcas open, only our own datadir's lock files say which one is us.
    cache = os.path.join(_orca_data_dir(), "cache")
    try:
        locks = sorted((f for f in os.listdir(cache) if f.endswith(".lock")),
                       key=lambda f: os.path.getmtime(os.path.join(cache, f)), reverse=True)
    except OSError:
        locks = []
    inst = next((f[:-5] for f in locks if f[:-5] in found), None)
    if inst is None:
        if len(found) > 1:
            return False, "%d OrcaSlicer instances running; can't tell which is this one" % len(found)
        inst = found[0]
    iface = "com.orcaslicer.OrcaSlicer.InstanceCheck.Object" + inst
    p = subprocess.run(
        ["dbus-send", "--session", "--type=method_call", "--dest=" + iface,
         "/com/orcaslicer/OrcaSlicer/InstanceCheck/Object" + inst,
         iface + ".AnotherInstance", "string:" + _instance_payload([path])],
        capture_output=True, text=True, timeout=15)
    if p.returncode != 0:
        return False, (p.stderr or "").strip()[:200]
    return True, ""


def out_dir():
    # The plugin audit hook allows writes under the data directory without a prompt.
    d = os.path.join(_orca_data_dir() or os.path.expanduser("~"), "support_fins")
    os.makedirs(d, exist_ok=True)
    return d


def web_root():
    """web/ beside this file (repo / install.sh), else the embedded copy, unpacked."""
    if os.path.isfile(os.path.join(WEB, "index.html")) or not _WEB_ZIP:
        return WEB
    import base64, io, zipfile, zlib
    d = os.path.join(out_dir(), "web-%08x" % zlib.crc32(_WEB_ZIP.encode()))
    if not os.path.isfile(os.path.join(d, "index.html")):
        tmp = d + ".tmp"
        zipfile.ZipFile(io.BytesIO(base64.b64decode(_WEB_ZIP))).extractall(tmp)
        os.replace(tmp, d)    # a half-extracted copy is never served
    return d


# ------------------------------------------------------------------ server

class Session:
    """What one editor window works on: a snapshot of the plate at open time."""

    def __init__(self, objects, settings, deliver):
        self.objects = objects        # [(name, stl bytes)]
        self.settings = settings
        self.deliver = deliver        # path -> (ok, detail)


class Handler(SimpleHTTPRequestHandler):
    session = None

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # a plugin update must show at once
        super().end_headers()

    def _reply(self, code, body, ctype="text/plain; charset=utf-8"):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s, path = self.session, urllib.parse.urlparse(self.path).path
        if path == "/orca/session":
            info = dict(s.settings, objects=[{"name": n} for n, _ in s.objects])
            return self._reply(200, json.dumps(info), "application/json")
        m = re.fullmatch(r"/orca/mesh/(\d+)/[^/]*\.stl", path)
        if m:
            i = int(m.group(1))
            if i >= len(s.objects):
                return self._reply(404, "no such object")
            return self._reply(200, s.objects[i][1], "model/stl")
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/orca/result":
            return self._reply(404, "not found")
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if len(data) < 84:
            return self._reply(400, "empty STL")
        name = urllib.parse.parse_qs(u.query).get("name", ["part"])[0]
        name = re.sub(r"[^\w.\- ]", "_", name).strip() or "part"
        path = os.path.join(out_dir(), name + "-fins.stl")
        with open(path, "wb") as fh:
            fh.write(data)
        ok, detail = self.session.deliver(path)
        _log("result %s (%d bytes) -> %s %s" % (path, len(data), ok, detail))
        if ok:
            return self._reply(200, "Loaded onto the plate: %s\n\nThe original part is still "
                                    "there -- delete it before slicing." % os.path.basename(path))
        return self._reply(500, "Saved %s but could not load it (%s)." % (path, detail))


def serve(session, web=WEB):
    """Start a loopback server for `session`; returns the server (port in .server_port)."""
    handler = functools.partial(type("H", (Handler,), {"session": session}), directory=web)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# The plugin window is loaded with SetPage (file:// base), so it just hops to the
# loopback server, where the ES modules and the fin worker load as on the web.
REDIRECT = """<!doctype html><meta charset="utf-8">
<body style="background:#0d1117;color:#8b949e;font:14px system-ui;padding:24px">
Opening Support Fins…
<script>location.replace("http://127.0.0.1:%d/index.html?orca=1");</script>"""


if orca is not None:

    class SupportFinsScript(orca.script.ScriptPluginCapabilityBase):
        win = None
        srv = None

        def get_name(self):
            return "Support Fins"

        def execute(self):
            model = orca.host.model()
            objects = []
            for obj in model.objects():
                if obj.instance_count() == 0:
                    continue
                tris = world_triangles(obj)
                if len(tris):
                    # Orca names an object after its file; the result gets "-fins.stl" added
                    name = re.sub(r"\.(stl|3mf|obj|stp|step|amf)$", "", obj.name or "", flags=re.I) or "part"
                    objects.append((name, stl_bytes(tris, name)))
            if not objects:
                orca.host.ui.message("Add a part to the plate first, orient it the way you "
                                     "want to print it, then run Support Fins.",
                                     "Support Fins", "ok", "warning")
                return orca.ExecutionResult.skipped("empty plate")

            if self.win is not None and self.win.is_open():
                self.win.close()
            if self.srv is not None:
                self.srv.shutdown()
            self.srv = serve(Session(objects, slicer_settings(), load_in_orca), web_root())
            _log("serving %d object(s) on port %d" % (len(objects), self.srv.server_port))
            self.win = orca.host.ui.create_window(
                html=REDIRECT % self.srv.server_port, title="Support Fins",
                width=1400, height=900, on_close=self.on_close)
            return orca.ExecutionResult.success()

        def on_close(self):
            self.win = None
            if self.srv is not None:
                # shutdown() blocks until serve_forever returns, so not on the UI thread
                threading.Thread(target=self.srv.shutdown, daemon=True).start()
                self.srv = None

    @orca.plugin
    class SupportFinsPlugin(orca.base):
        def register_capabilities(self):
            orca.register_capability(SupportFinsScript)
