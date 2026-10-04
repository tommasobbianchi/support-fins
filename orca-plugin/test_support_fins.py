"""Offline check of the plugin's Python half: python3 orca-plugin/test_support_fins.py

Stubs `orca` in sys.modules BEFORE importing support_fins, so the capability
class defined under `if orca is not None` is importable and drivable.
"""
import base64
import os
import struct
import sys
import types

import numpy as np

class Panel:
    def __init__(self): self.posts = []
    def post(self, m): self.posts.append(m)
    def show(self): pass
    def hide(self): pass
    def is_open(self): return True

created = {}          # what create_dock_panel was called with / what it returned

def _make_panel(**kw):
    p = Panel()
    created["panel"] = p
    created["html"] = kw["html"]
    created["kw"] = kw
    return p

orca_mod = types.ModuleType("orca")
class _Base: pass
class _CapBase: pass
class _Result: pass
orca_mod.base = _Base
orca_mod.script = types.SimpleNamespace(ScriptPluginCapabilityBase=_CapBase,
                                        register_capability=lambda c: None)
orca_mod.plugin = lambda c: c
orca_mod.ExecutionResult = types.SimpleNamespace(success=lambda *a: ("success",),
                                                 skipped=lambda *a: ("skipped",))
orca_mod.host = types.SimpleNamespace(
    message=lambda *a: (_ for _ in ()).throw(AssertionError("ui.message called")),
)
orca_mod.host.ui = types.SimpleNamespace(
    message=lambda *a: (_ for _ in ()).throw(AssertionError("ui.message called")),
    create_dock_panel=_make_panel)
orca_mod.host.model = None                      # set per-test
sys.modules["orca"] = orca_mod

import support_fins as sf


class Mesh:
    def __init__(self, v, t): self.v, self.t = v, t
    def vertices(self): return self.v
    def triangles(self): return self.t


class Vol:
    def __init__(self, mesh, m, part=True): self._m, self._mat, self._part = mesh, m, part
    def is_model_part(self): return self._part
    def mesh(self): return self._m
    def matrix(self): return self._mat


class Inst:
    def __init__(self, m): self._m = m
    def matrix(self): return self._m


class Obj:
    name = "cube"
    def __init__(self, vols, inst): self._v, self._i = vols, inst
    def volumes(self): return self._v
    def instance(self, i): return self._i
    def instance_count(self): return 1


def main():
    tri = Mesh(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32), np.array([[0, 1, 2]], np.int32))
    move = np.eye(4); move[:3, 3] = [10, 20, 30]
    mirror = np.diag([-1.0, 1, 1, 1])
    obj = Obj([Vol(tri, np.eye(4)), Vol(tri, mirror), Vol(tri, np.eye(4), part=False)], Inst(move))

    w = sf.world_triangles(obj)
    assert w.shape == (2, 3, 3), w.shape                    # modifier skipped
    assert np.allclose(w[0], [[10, 20, 30], [11, 20, 30], [10, 21, 30]])
    n = [np.cross(t[1] - t[0], t[2] - t[0])[2] for t in w]
    assert n[0] > 0 and n[1] > 0, n                          # mirror winding restored

    stl = sf.stl_bytes(w, "cube")
    assert len(stl) == 84 + 50 * 2 and struct.unpack("<I", stl[80:84])[0] == 2
    assert struct.unpack("<3f", stl[84 + 12:84 + 24]) == (10.0, 20.0, 30.0)

    # ---------------------------------------------------------------- page build
    panel_html = sf._panel_html()
    assert 'window.__SF_FINWORKER__ = ' in panel_html       # finworker bridge
    assert panel_html.count("<script>") == 2 and panel_html.count("</script>") == 2
    assert "<style>" in panel_html and '<div id="viewport"></div>' in panel_html
    assert 'type="importmap"' not in panel_html and '<script type="module"' not in panel_html
    # the worker source lives in a json string; no raw </script may hide in any tag
    assert panel_html.count("</script>") == 2
    build = os.path.join(os.path.dirname(sf.__file__), "build", "web")
    app = open(os.path.join(build, "app.js"), encoding="utf-8").read()
    assert app in panel_html                                  # app bundle inlined verbatim
    # the finworker bundle is the json.dumps'd source; a chunk of it must round-trip
    import json as _json
    fw_json = panel_html.split('window.__SF_FINWORKER__ = ')[1].split(";</script>")[0]
    assert _json.loads(fw_json).startswith(open(os.path.join(build, "finworker.js"), encoding="utf-8").read()[:200])

    # ----------------------------------------------------- execute(), refresh, result
    sf._cfg = lambda key: {"layer_height": "0.2", "filament_type": "Petg",
                           "printable_area": "0x0,256x0,256x256,0x256",
                           "printable_height": "256"}[key]
    orca_mod.host.model = lambda: types.SimpleNamespace(objects=lambda: [obj])

    S = sf.SupportFinsScript
    assert issubclass(S, _CapBase)
    s = S()

    # the hand-off must be exercised: stub it, remember the delivered path
    delivered = []
    def _deliver(p):
        delivered.append(p)
        return True, ""
    sf.load_in_orca = _deliver

    s.execute()
    assert created["html"] == panel_html
    assert created["panel"] is s.panel
    # first run: the page is not armed yet, so NO session is posted immediately
    assert created["panel"].posts == [], created["panel"].posts
    # the page posts {ready} once its onMessage listener is live -> session is sent
    s.on_message({"command": "ready"})
    session = created["panel"].posts[-1]
    assert session["command"] == "session"
    assert session["session"]["material"] == "petg"          # Petg -> petg profile
    assert session["session"]["layer_height"] == 0.2
    assert session["session"]["volume"] == [256, 256, 256]
    assert session["objects"] == [{"name": "cube", "stl": base64.b64encode(stl).decode()}]

    # a result: base64 decode -> file written -> hand-off called -> {loaded, ok}
    s.on_message({"command": "result", "name": "cu/be", "b64": base64.b64encode(stl).decode()})
    assert delivered and delivered[0].endswith("cu_be-fins.stl"), delivered
    assert open(delivered[0], "rb").read() == stl
    last = created["panel"].posts[-1]
    assert last["command"] == "loaded" and last["ok"] and "Loaded onto the plate" in last["detail"]
    assert "cu_be-fins.stl" in last["detail"]

    # a bad result reports ok=False
    s.on_message({"command": "result", "name": "x", "b64": "AAAA"})
    assert created["panel"].posts[-1]["ok"] is False

    # a second run refreshes the open panel in place (no second panel created)
    n = created["panel"]
    s.execute()
    assert created["panel"] is n and created["panel"].posts[-1]["command"] == "session"

    print("ok")


if __name__ == "__main__":
    main()
