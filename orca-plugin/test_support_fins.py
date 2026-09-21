"""Offline check of the plugin's Python half: python3 orca-plugin/test_support_fins.py"""
import json
import struct
import urllib.request

import numpy as np

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

    got = []
    session = sf.Session([("cube", stl)], {"layer_height": 0.2}, lambda p: (got.append(p), (True, ""))[1])
    srv = sf.serve(session)
    base = "http://127.0.0.1:%d" % srv.server_port
    info = json.load(urllib.request.urlopen(base + "/orca/session"))
    assert info == {"layer_height": 0.2, "objects": [{"name": "cube"}]}, info
    assert urllib.request.urlopen(base + "/orca/mesh/0/cube.stl").read() == stl
    js = urllib.request.urlopen(base + "/app.js")
    assert "javascript" in js.headers["Content-Type"]        # module scripts need a JS MIME type
    req = urllib.request.Request(base + "/orca/result?name=cu/be", data=stl, method="POST")
    assert b"Loaded" in urllib.request.urlopen(req).read()
    assert got and got[0].endswith("cu_be-fins.stl") and open(got[0], "rb").read() == stl
    srv.shutdown()
    print("ok")


if __name__ == "__main__":
    main()
