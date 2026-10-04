// The plugin entry must run the SAME engine as the website, and its output
// must keep every promise the upstream suite pins.
//
// In Orca the user poses the part with Orca's own gizmo, so the plugin hands the
// engine an ALREADY-POSED mesh parked somewhere on the plate. Two claims:
//   1. Same engine: for a posed mesh, the Orca path places the same fins as the
//      website run on that same posed mesh, regardless of plate position.
//   2. Same promises: watertight, wall clears the part, tined fins placed, fins
//      stand on the part's bed.
//
//   deno test --allow-read plugins/shared/tests/
import { computeFins } from '../engine/fins_entry.js';
import { readSTL, MODELS, analyze, fins, rotX, rotY, assert, assertClose, block,
         isClosed, insideCount } from '../../../tests/_util.js';
import { buildTopology, IDENTITY3 } from '../../../web/overhangs.js';

const OPTS = { mode: 'auto', bedPad: true, tines: true, tineDensity: 0, coverage: 0.5, layerHeight: 0.2 };
const topoOf = (pos) => buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });

// three.js Matrix3.elements are column-major: v' = M v. Float64: Orca poses in double.
function bake(pos, m, dx = 0, dy = 0, dz = 0) {
  const out = new Float64Array(pos.length);
  for (let i = 0; i < pos.length; i += 3) {
    const x = pos[i], y = pos[i + 1], z = pos[i + 2];
    out[i] = m[0] * x + m[3] * y + m[6] * z + dx;
    out[i + 1] = m[1] * x + m[4] * y + m[7] * z + dy;
    out[i + 2] = m[2] * x + m[5] * y + m[8] * z + dz;
  }
  return out;
}
// What printfins.com does with an STL that was saved already posed (identity rotation).
function centred(pos) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, z0 = Infinity;
  for (let i = 0; i < pos.length; i += 3) {
    x0 = Math.min(x0, pos[i]); x1 = Math.max(x1, pos[i]);
    y0 = Math.min(y0, pos[i + 1]); y1 = Math.max(y1, pos[i + 1]); z0 = Math.min(z0, pos[i + 2]);
  }
  const o = new Float64Array(pos.length);
  for (let i = 0; i < pos.length; i += 3) {
    o[i] = pos[i] - (x0 + x1) / 2; o[i + 1] = pos[i + 1] - (y0 + y1) / 2; o[i + 2] = pos[i + 2] - z0;
  }
  return o;
}
const flat = (b) => [...b.triangles, ...(b.padTriangles || [])].flatMap((v) => [v[0], v[1], v[2]]);
const PLATE = [137.25, 88.5, 3.0];   // an arbitrary spot on Orca's plate
const load = (name) => readSTL(Deno.readFileSync(`${MODELS}${name}.stl`));

const CASES = [['cube', 'X45', rotX(45)], ['ramp', 'X45', rotX(45)], ['wedge', 'X45', rotX(45)],
               ['lbracket', 'Y20', rotY(20)], ['lbracket', 'Y35', rotY(35)], ['lbracket', 'Y50', rotY(50)]];

for (const [name, pose, rot] of CASES) {
  Deno.test(`${name}/${pose}: Orca path places the same fins as the website, anywhere on the plate`, () => {
    // Bit-equality is NOT a fair bar: the upstream engine's tine placement moves
    // under 1e-13 mm of coordinate noise (see ENGINE-SENSITIVITY.md -- lbracket@Y50
    // goes 16 -> 19 tines between two float-identical-to-print poses). So we pin
    // what matters: the same overhangs, the same number of fins, tines within 3.
    const posedAtOrigin = centred(bake(load(name), rot));
    const topo = topoOf(posedAtOrigin);
    const r = analyze(topo, 45, IDENTITY3);
    const web = fins.buildFins(topo, r, IDENTITY3, OPTS);
    const got = computeFins(bake(load(name), rot, ...PLATE), OPTS);
    assert(got.stats.overhangRegions === r.regions.length, 'overhang analysis differs');
    assert(got.stats.braces === web.braceCount, `fin count ${got.stats.braces} vs ${web.braceCount}`);
    assert(Math.abs(got.stats.tines - web.tines) <= 3, `tines ${got.stats.tines} vs ${web.tines}`);
  });

  Deno.test(`${name}/${pose}: upstream promises hold on the Orca path`, () => {
    const posed = bake(load(name), rot, ...PLATE);
    const res = computeFins(posed, OPTS);
    assert(res.stats.braces >= 1, 'no fins placed');
    assert(res.stats.tines >= 1, 'fins placed without tines (not gripping)');
    const tri = (a) => { const v = []; for (let i = 0; i < a.length; i += 3) v.push([a[i], a[i + 1], a[i + 2]]); return v; };
    const finV = tri(res.triangles.subarray(0, res.stats.finTriangles * 9));
    const padV = tri(res.triangles.subarray(res.stats.finTriangles * 9));
    assert(isClosed(finV), 'fin geometry is not closed');
    assert(isClosed(padV), 'pad geometry is not closed');
    // Walls only (no tines): must clear the part, exactly as upstream pins it.
    const walls = computeFins(posed, { ...OPTS, tines: false });
    const seated = centred(posed);
    const inside = insideCount(topoOf(seated), IDENTITY3, { x: 0, y: 0, z: 0 },
      tri(walls.triangles.subarray(0, walls.stats.finTriangles * 9)));
    assert(inside === 0, `${inside} wall verts inside the part`);
  });

  Deno.test(`${name}/${pose}: offset maps fins back under the part on the plate`, () => {
    const posed = bake(load(name), rot, ...PLATE);
    const { triangles: all, offset, stats } = computeFins(posed, OPTS);
    const triangles = all.subarray(0, stats.finTriangles * 9); // fin walls; the pad skirts wider
    const off = [offset.x, offset.y, offset.z];
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    const plo = [Infinity, Infinity, Infinity], phi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < triangles.length; i += 3) for (let k = 0; k < 3; k++) {
      const w = triangles[i + k] - off[k];
      lo[k] = Math.min(lo[k], w); hi[k] = Math.max(hi[k], w);
    }
    for (let i = 0; i < posed.length; i += 3) for (let k = 0; k < 3; k++) {
      plo[k] = Math.min(plo[k], posed[i + k]); phi[k] = Math.max(phi[k], posed[i + k]);
    }
    assertClose(lo[2], plo[2], 1e-3, 'fin base is not on the part\'s bed');
    assert(hi[2] <= phi[2] + 1e-3, 'fins rise above the part');
    for (const k of [0, 1]) assert(lo[k] > plo[k] - 15 && hi[k] < phi[k] + 15, `fins off the part on axis ${k}`);
  });
}

Deno.test('lbracket/Y35: website rot-param vs pre-posed: same overhangs and fins', () => {
  const raw = load('lbracket'), rot = rotY(35), topo = topoOf(raw);
  const r = analyze(topo, 45, rot), web = fins.buildFins(topo, r, rot, OPTS);
  const got = computeFins(bake(raw, rot, ...PLATE), OPTS);
  assert(got.stats.overhangRegions === r.regions.length, 'overhang analysis differs');
  assert(got.stats.braces === web.braceCount, 'fin count differs');
  assert(Math.abs(got.stats.tines - web.tines) <= 3, `tines ${got.stats.tines} vs ${web.tines}`);
});

Deno.test('moving a part around the plate never changes its fins', () => {
  for (const [name, , rot] of CASES) {
    const ref = computeFins(bake(load(name), rot, ...PLATE), OPTS).triangles;
    for (const spot of [[0, 0, 0], [12.3456789, -250.1, 0], [300.07, 299.93, 0.4], [-0.000001, 55.5, 0]]) {
      const t = computeFins(bake(load(name), rot, ...spot), OPTS).triangles;
      assert(t.length === ref.length, `${name}: ${t.length / 9} vs ${ref.length / 9} tris at ${spot}`);
      let worst = 0;
      for (let i = 0; i < t.length; i++) worst = Math.max(worst, Math.abs(t[i] - ref[i]));
      assert(worst < 1e-4, `${name}: fins moved by ${worst} mm at ${spot}`);
    }
  }
});

Deno.test('rejects malformed input', () => {
  let threw = false;
  try { computeFins(new Float32Array(10)); } catch { threw = true; }
  assert(threw, 'accepted a soup that is not a multiple of 9');
});

// ---- sway braces -----------------------------------------------------------
// The website's braces (web/sway.js) are reachable through the entry point only
// because `sway` is on the options list it forwards: everything absent from that
// list is dropped before buildFins ever sees it, which is how the feature shipped
// on the site and stayed unreachable from every plugin.

/** A tall, slender post as a posed soup -- the case sway braces exist for. */
function post(h = 150) {
  const pos = block(-20, 20, -15, 15, 0, h);
  return Float64Array.from(pos);
}

Deno.test('sway: off by default -- a host that says nothing gets exactly what it got before', () => {
  const soup = post();
  const plain = computeFins(soup, OPTS);
  const explicit = computeFins(soup, { ...OPTS, sway: null });
  assert(plain.stats.swayBraces === 0, `braces nobody asked for (${plain.stats.swayBraces})`);
  assert(plain.triangles.length === explicit.triangles.length, 'sway:null changed the output');
});

Deno.test('sway: { on: true } braces a tall part, and says so in the stats', () => {
  const soup = post();
  const off = computeFins(soup, OPTS);
  const on = computeFins(soup, { ...OPTS, sway: { on: true } });
  assert(on.stats.swayBraces >= 2, `expected braces, got ${on.stats.swayBraces} (${on.stats.swayReason})`);
  assert(on.stats.swayTines > 0, 'braces placed without tines');
  assert(on.triangles.length > off.triangles.length, 'braces were not added to the geometry');
  // counted apart from the fins, which serve overhangs and are a different promise
  assert(on.stats.braces === off.stats.braces, 'brace count leaked into the fin count');
});

Deno.test('sway: the engine agrees with the website, brace for brace', () => {
  const soup = post();
  const topo = topoOf(soup);
  const web = fins.buildFins(topo, analyze(topo, OPTS.threshold ?? 45, IDENTITY3), IDENTITY3,
                             { ...OPTS, sway: { on: true } });
  const got = computeFins(soup, { ...OPTS, sway: { on: true } });
  assert(got.stats.swayBraces === web.sway.count,
         `braces ${got.stats.swayBraces} vs the website's ${web.sway.count}`);
  assert(got.stats.swayTines === web.sway.tines,
         `brace tines ${got.stats.swayTines} vs the website's ${web.sway.tines}`);
});

Deno.test('sway: the host\u2019s settings reach the braces', () => {
  const soup = post();
  const loose = computeFins(soup, { ...OPTS, sway: { on: true, tineSpacing: 20 } });
  const tight = computeFins(soup, { ...OPTS, sway: { on: true, tineSpacing: 4 } });
  assert(tight.stats.swayTines > loose.stats.swayTines,
         `tine spacing ignored (${tight.stats.swayTines} at 4mm vs ${loose.stats.swayTines} at 20mm)`);
  const shallow = computeFins(soup, { ...OPTS, sway: { on: true, gripFrom: 120 } });
  assert(shallow.stats.swayTines < loose.stats.swayTines, '"grip from" ignored');
});

Deno.test('sway: a part too short to brace reports why, and still builds its fins', () => {
  const short = Float64Array.from(block(-20, 20, -15, 15, 0, 20));
  const r = computeFins(short, { ...OPTS, sway: { on: true } });
  assert(r.stats.swayBraces === 0, `a 20mm part got ${r.stats.swayBraces} braces`);
  assert(typeof r.stats.swayReason === 'string' && r.stats.swayReason,
         'no reason given for placing none');
});
