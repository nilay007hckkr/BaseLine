/**
 * The onboarding run (three.js): an original wireframe runner. A ship flies over an endless
 * vector grid between wireframe hills, collecting shards and dodging blocks, and eases to a stop
 * in front of each numbered gate so the tour can explain one feature there. Hitting a block only
 * glitches the screen and slows the ship; the run never ends early.
 */
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

export interface RunStats {
  distance: number;
  shards: number;
  hits: number;
}

export interface Runner {
  /** Fly on to the next gate. */
  launch(): void;
  dispose(): void;
}

interface Options {
  gates: number;
  onGate: (index: number) => void; // stopped in front of gate `index`
  onStats: (stats: RunStats) => void;
  onHit: () => void;
  reducedMotion?: boolean;
}

const GREEN = new THREE.Color(0x39e75f);
const WHITE = new THREE.Color(0xffffff);
const RED = 0xff4d4d;

const CELL = 4; // grid cell, in scene units ("metres")
const COLS = 40; // grid columns across x in [-80, 80]
const ROWS = 45; // grid rows along z in [8, -172]
const HALF_TRACK = 8; // the flat track runs from -8 to 8
const SHIP_LIMIT = 6.4;
const LANES = [-4.6, 0, 4.6];
const FIRST_GATE = 110;
const GATE_GAP = 150;
const STOP_BEFORE = 16; // the ship stops this far in front of a gate
const BRAKE = 55; // deceleration when stopping, units/s²
const CRUISE = 24;
const BOOST = 1.7;
const IDLE = 2;
const IDLE_LIMIT = 30; // the intro drift stops here
const AHEAD = 175; // spawn objects this far ahead

// 5x7 dot-matrix digits for the gate numbers
const DIGITS: Record<string, string[]> = {
  "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
  "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
  "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
  "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
  "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
  "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
  "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
  "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
  "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
  "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
};

/** Deterministic random numbers so every run has the same course. */
function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** Hill height at grid position x and course position w; the track itself stays flat. */
function hill(x: number, w: number): number {
  const ax = Math.abs(x);
  if (ax <= HALF_TRACK + 1) return 0;
  const ramp = Math.min(1, (ax - HALF_TRACK - 1) / 24);
  const n =
    0.55 +
    0.25 * Math.sin(w * 0.045 + x * 0.11) +
    0.15 * Math.sin(w * 0.13 - x * 0.27) +
    0.1 * Math.sin(w * 0.31 + x * 0.07);
  return ramp * ramp * 17 * n;
}

function lines(points: number[][], pairs: number[][]): THREE.BufferGeometry {
  const pos: number[] = [];
  for (const [a, b] of pairs) {
    pos.push(...(points[a ?? 0] ?? []), ...(points[b ?? 0] ?? []));
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  return g;
}

function shipGeometry(): THREE.BufferGeometry {
  const p = [
    [0, 0.05, -2.2], // 0 nose
    [-1.25, -0.05, 0.75], // 1 left wing tip
    [1.25, -0.05, 0.75], // 2 right wing tip
    [0, 0.38, 0.05], // 3 canopy
    [0, 0.08, 0.75], // 4 tail
  ];
  // prettier-ignore
  const pairs = [[0, 1], [0, 2], [0, 3], [1, 3], [2, 3], [1, 4], [2, 4], [3, 4]];
  return lines(p, pairs);
}

/** A box-shaped portal: two frames joined at the corners. */
function gateGeometry(): THREE.BufferGeometry {
  const w = HALF_TRACK + 1;
  const h = 8;
  // prettier-ignore
  const p = [
    [-w, 0, 0], [-w, h, 0], [w, h, 0], [w, 0, 0],
    [-w, 0, -1.6], [-w, h, -1.6], [w, h, -1.6], [w, 0, -1.6],
  ];
  // prettier-ignore
  const pairs = [
    [0, 1], [1, 2], [2, 3], [4, 5], [5, 6], [6, 7], [0, 4], [1, 5], [2, 6], [3, 7],
  ];
  return lines(p, pairs);
}

/** A two-digit gate number as a grid of square dots, centred on x = 0. */
function numberGeometry(n: number): THREE.BufferGeometry {
  const text = String(n).padStart(2, "0");
  const pitch = 0.42;
  const cols = text.length * 6 - 1;
  const pos: number[] = [];
  for (let i = 0; i < text.length; i++) {
    (DIGITS[text.charAt(i)] ?? []).forEach((row, r) => {
      for (let c = 0; c < row.length; c++) {
        if (row.charAt(c) === "1")
          pos.push((i * 6 + c - (cols - 1) / 2) * pitch, (6 - r) * pitch, 0);
      }
    });
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  return g;
}

interface Thing {
  kind: "shard" | "block";
  at: number; // course position where the ship meets it
  obj: THREE.LineSegments;
  done: boolean; // collected or hit
  fade: number;
}

interface Gate {
  at: number;
  frame: THREE.LineSegments;
  digits: THREE.Points;
  flash: number;
}

export function createRunner(el: HTMLElement, opts: Options): Runner | null {
  if (!webglAvailable()) return null;
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  } catch {
    return null;
  }
  const reduced = opts.reducedMotion ?? false;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.setClearColor(0x000000, 1);
  el.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x000000, 22, 150);
  const camera = new THREE.PerspectiveCamera(70, 1, 0.1, 400);
  camera.position.set(0, 3.9, 9.5);

  const target = new THREE.WebGLRenderTarget(1, 1, { samples: 4, type: THREE.HalfFloatType });
  const composer = new EffectComposer(renderer, target);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.6, 0.35, 0.28);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  // ---- the grid: flat track in the middle, hills on both sides ----
  const vx = COLS + 1;
  const vz = ROWS + 1;
  const gridPos = new Float32Array(vx * vz * 3);
  const gridCol = new Float32Array(vx * vz * 3);
  const index: number[] = [];
  for (let j = 0; j < vz; j++) {
    for (let i = 0; i < vx; i++) {
      const k = j * vx + i;
      gridPos[k * 3] = -80 + i * CELL;
      gridPos[k * 3 + 2] = 8 - j * CELL;
      if (i < COLS) index.push(k, k + 1);
      if (j < ROWS) index.push(k, k + vx);
    }
  }
  const gridGeo = new THREE.BufferGeometry();
  const gridPosAttr = new THREE.BufferAttribute(gridPos, 3);
  const gridColAttr = new THREE.BufferAttribute(gridCol, 3);
  gridGeo.setAttribute("position", gridPosAttr);
  gridGeo.setAttribute("color", gridColAttr);
  gridGeo.setIndex(index);
  const grid = new THREE.LineSegments(gridGeo, new THREE.LineBasicMaterial({ vertexColors: true }));
  scene.add(grid);

  function shapeGrid(d: number) {
    const offset = d % CELL;
    grid.position.z = offset;
    for (let k = 0; k < vx * vz; k++) {
      const x = gridPos[k * 3] ?? 0;
      const w = (gridPos[k * 3 + 2] ?? 0) + offset - d;
      const h = hill(x, w);
      gridPos[k * 3 + 1] = h;
      const b = 0.2 + 0.5 * Math.min(1, h / 12);
      gridCol[k * 3] = GREEN.r * b;
      gridCol[k * 3 + 1] = GREEN.g * b;
      gridCol[k * 3 + 2] = GREEN.b * b;
    }
    gridPosAttr.needsUpdate = true;
    gridColAttr.needsUpdate = true;
  }

  const edgeMat = new THREE.LineBasicMaterial({ color: GREEN });
  const edges = new THREE.LineSegments(
    lines(
      [
        [-HALF_TRACK, 0.02, 8],
        [-HALF_TRACK, 0.02, -172],
        [HALF_TRACK, 0.02, 8],
        [HALF_TRACK, 0.02, -172],
      ],
      [
        [0, 1],
        [2, 3],
      ],
    ),
    edgeMat,
  );
  scene.add(edges);

  // ---- sky: a few still stars beyond the fog ----
  const rand = rng(7);
  const starPos: number[] = [];
  for (let i = 0; i < 260; i++) {
    starPos.push((rand() - 0.5) * 700, 18 + rand() * 170, -320);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute("position", new THREE.Float32BufferAttribute(starPos, 3));
  scene.add(
    new THREE.Points(
      starGeo,
      new THREE.PointsMaterial({ color: 0x6e6e6e, size: 1.4, sizeAttenuation: false, fog: false }),
    ),
  );

  // ---- speed streaks ----
  const STREAKS = 36;
  const streaks: { x: number; y: number; z: number }[] = [];
  for (let i = 0; i < STREAKS; i++) {
    let x = (rand() - 0.5) * 52;
    const y = 1.5 + rand() * 8;
    if (Math.abs(x) < 3 && y < 3) x += 6;
    streaks.push({ x, y, z: -160 + rand() * 170 });
  }
  const streakPos = new Float32Array(STREAKS * 6);
  const streakGeo = new THREE.BufferGeometry();
  const streakAttr = new THREE.BufferAttribute(streakPos, 3);
  streakGeo.setAttribute("position", streakAttr);
  scene.add(
    new THREE.LineSegments(
      streakGeo,
      new THREE.LineBasicMaterial({ color: WHITE, transparent: true, opacity: 0.35 }),
    ),
  );

  // ---- the ship and its trails ----
  const ship = new THREE.Group();
  ship.add(new THREE.LineSegments(shipGeometry(), new THREE.LineBasicMaterial({ color: WHITE })));
  ship.add(
    new THREE.LineSegments(
      lines(
        [
          [-0.35, 0.1, 0.78],
          [0.35, 0.1, 0.78],
        ],
        [[0, 1]],
      ),
      new THREE.LineBasicMaterial({ color: GREEN }),
    ),
  );
  scene.add(ship);

  const TRAIL = 16;
  const history: number[] = Array.from({ length: TRAIL }, () => 0);
  const trailPos = new Float32Array(TRAIL * 2 * 3);
  const trailCol = new Float32Array(TRAIL * 2 * 3);
  const trailIdx: number[] = [];
  for (let side = 0; side < 2; side++) {
    for (let k = 0; k < TRAIL; k++) {
      const v = side * TRAIL + k;
      const fade = 1 - k / (TRAIL - 1);
      trailCol[v * 3] = GREEN.r * fade;
      trailCol[v * 3 + 1] = GREEN.g * fade;
      trailCol[v * 3 + 2] = GREEN.b * fade;
      if (k < TRAIL - 1) trailIdx.push(v, v + 1);
    }
  }
  const trailGeo = new THREE.BufferGeometry();
  const trailAttr = new THREE.BufferAttribute(trailPos, 3);
  trailGeo.setAttribute("position", trailAttr);
  trailGeo.setAttribute("color", new THREE.BufferAttribute(trailCol, 3));
  trailGeo.setIndex(trailIdx);
  scene.add(new THREE.LineSegments(trailGeo, new THREE.LineBasicMaterial({ vertexColors: true })));

  // ---- gates ----
  const gateGeo = gateGeometry();
  const gates: Gate[] = [];
  for (let i = 0; i < opts.gates; i++) {
    const at = FIRST_GATE + i * GATE_GAP;
    const frame = new THREE.LineSegments(gateGeo, new THREE.LineBasicMaterial({ color: GREEN }));
    const digits = new THREE.Points(
      numberGeometry(i + 1),
      new THREE.PointsMaterial({ color: GREEN, size: 0.34 }),
    );
    digits.position.y = 9;
    frame.add(digits);
    scene.add(frame);
    gates.push({ at, frame, digits, flash: 0 });
  }

  // ---- shards and blocks ----
  const shardGeo = new THREE.EdgesGeometry(new THREE.OctahedronGeometry(0.45));
  const shardMat = new THREE.LineBasicMaterial({ color: GREEN });
  const blockGeo = new THREE.EdgesGeometry(new THREE.BoxGeometry(2.4, 2.4, 2.4));
  const blockMat = new THREE.LineBasicMaterial({ color: 0xdddddd });
  const hitMat = new THREE.LineBasicMaterial({ color: RED });
  const things: Thing[] = [];
  const course = rng(42);
  let nextRow = 36;
  const lastAt = FIRST_GATE + (opts.gates - 1) * GATE_GAP + 40;

  // keep the stretch in front of each gate clear: rows run up to ~18 units long
  function nearGate(at: number): boolean {
    return gates.some((g) => at > g.at - 48 && at < g.at + 8);
  }
  function shard(x: number, at: number) {
    const obj = new THREE.LineSegments(shardGeo, shardMat);
    obj.position.set(x, 1, 0);
    scene.add(obj);
    things.push({ kind: "shard", at, obj, done: false, fade: 1 });
  }
  function block(x: number, at: number, tall: number) {
    const obj = new THREE.LineSegments(blockGeo, blockMat);
    obj.scale.y = tall;
    obj.position.set(x, 1.2 * tall, 0);
    scene.add(obj);
    things.push({ kind: "block", at, obj, done: false, fade: 1 });
  }
  function lane(): number {
    return LANES[Math.floor(course() * LANES.length)] ?? 0;
  }
  function spawn(d: number) {
    while (nextRow < d + AHEAD && nextRow < lastAt) {
      const at = nextRow;
      nextRow += 14 + course() * 10;
      if (nearGate(at)) continue;
      const r = course();
      if (at < 60 || r < 0.36) {
        const x = lane();
        for (let k = 0; k < 5; k++) shard(x, at + k * 2.6);
      } else if (r < 0.56) {
        const phase = course() * Math.PI * 2;
        for (let k = 0; k < 9; k++) shard(Math.sin(k * 0.55 + phase) * 5, at + k * 2.2);
      } else if (r < 0.82) {
        const gap = Math.floor(course() * LANES.length);
        LANES.forEach((x, i) => {
          if (i === gap) for (let k = -1; k <= 1; k++) shard(x, at + k * 2.8);
          else block(x, at, 1 + course() * 0.8);
        });
      } else {
        const x = lane();
        block(x, at, 1.6 + course() * 0.8);
        shard(x === 0 ? (LANES[0] ?? -4.6) : 0, at);
      }
    }
  }

  // ---- input ----
  const keys = { left: false, right: false, boost: false };
  function setKey(event: KeyboardEvent, down: boolean) {
    if (event.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(event.target.tagName))
      return;
    const k = event.key.toLowerCase();
    if (k === "arrowleft" || k === "a") keys.left = down;
    else if (k === "arrowright" || k === "d") keys.right = down;
    else if (k === "arrowup" || k === "w" || k === "shift") keys.boost = down;
  }
  const onKeyDown = (e: KeyboardEvent) => {
    setKey(e, true);
  };
  const onKeyUp = (e: KeyboardEvent) => {
    setKey(e, false);
  };
  let pointer: number | null = null; // -1 left, 1 right
  const onPointer = (e: PointerEvent) => {
    if (e.type === "pointerdown" || (pointer !== null && e.type === "pointermove")) {
      const rect = el.getBoundingClientRect();
      pointer = e.clientX < rect.left + rect.width / 2 ? -1 : 1;
    } else if (e.type !== "pointermove") {
      pointer = null;
    }
  };
  const POINTER = [
    "pointerdown",
    "pointermove",
    "pointerup",
    "pointercancel",
    "pointerleave",
  ] as const;
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  for (const t of POINTER) el.addEventListener(t, onPointer);

  // ---- size ----
  function resize() {
    const w = Math.max(1, el.clientWidth);
    const h = Math.max(1, el.clientHeight);
    renderer.setSize(w, h);
    composer.setSize(w, h);
    bloom.resolution.set(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();
  const observer = new ResizeObserver(resize);
  observer.observe(el);

  // ---- state ----
  let mode: "idle" | "run" | "stop" = "idle";
  let next = 0; // the gate the ship is heading for
  let d = 0;
  let speed = 0;
  let x = 0;
  let vxShip = 0;
  let shake = 0;
  let shards = 0;
  let hits = 0;
  let statsClock = 0;
  let camOffset = 0;
  let time = 0;
  let last = performance.now();

  function stopAt(): number {
    return next < opts.gates ? FIRST_GATE + next * GATE_GAP - STOP_BEFORE : Infinity;
  }

  function arrive() {
    mode = "stop";
    speed = 0;
    opts.onGate(next);
    next += 1;
  }

  function step(dt: number) {
    time += dt;
    // speed: cruise, boost, idle drift, and a smooth brake into the next stop
    const goal =
      mode === "run"
        ? CRUISE * (1 + next * 0.05) * (keys.boost ? BOOST : 1)
        : mode === "idle"
          ? IDLE
          : 0;
    speed += (goal - speed) * Math.min(1, dt * 2.2);
    const limit = mode === "idle" ? Math.min(IDLE_LIMIT, stopAt()) : stopAt();
    speed = Math.min(speed, Math.sqrt(2 * BRAKE * Math.max(0, limit - d)));
    const before = d;
    d = Math.min(d + speed * dt, limit);
    if (mode === "run" && stopAt() - d < 0.05) arrive();

    // steering
    const steer = mode === "run" ? (keys.right ? 1 : 0) - (keys.left ? 1 : 0) || (pointer ?? 0) : 0;
    vxShip += (steer * 15 - vxShip) * Math.min(1, dt * 9);
    x = Math.max(-SHIP_LIMIT, Math.min(SHIP_LIMIT, x + vxShip * dt));
    if (Math.abs(x) >= SHIP_LIMIT) vxShip = 0;
    ship.position.set(x, 0.9 + Math.sin(time * 2.2) * 0.08, 0);
    ship.rotation.z = -vxShip * 0.03;
    ship.rotation.y = -vxShip * 0.012;

    shapeGrid(d);
    spawn(d);

    // things: move, spin, collide, fade, remove
    for (let i = things.length - 1; i >= 0; i--) {
      const t = things[i];
      if (!t) continue;
      const z = d - t.at;
      t.obj.position.z = z;
      if (t.kind === "shard") {
        t.obj.rotation.y = time * 2 + t.at;
        if (t.done) {
          t.fade -= dt * 5;
          t.obj.position.y += dt * 6;
          t.obj.scale.setScalar(Math.max(0.01, t.fade));
        }
      }
      if (mode === "run" && !t.done && Math.abs(z) < 1.3) {
        const dx = Math.abs(x - t.obj.position.x);
        if (t.kind === "shard" && dx < 1.6) {
          t.done = true;
          shards += 1;
        } else if (t.kind === "block" && dx < 2.3) {
          t.done = true;
          t.obj.material = hitMat;
          hits += 1;
          speed *= 0.45;
          shake = 0.5;
          opts.onHit();
        }
      }
      if (z > 12 || t.fade <= 0) {
        scene.remove(t.obj);
        things.splice(i, 1);
      }
    }

    // gates: position, flash when the ship passes through
    for (const g of gates) {
      g.frame.position.z = d - g.at;
      if (before < g.at && d >= g.at) g.flash = 1;
      g.flash = Math.max(0, g.flash - dt * 1.6);
      const c = GREEN.clone().lerp(WHITE, g.flash);
      (g.frame.material as THREE.LineBasicMaterial).color.copy(c);
      (g.digits.material as THREE.PointsMaterial).color.copy(c);
    }

    // trails from the wing tips
    history.unshift(x);
    history.length = TRAIL;
    const spacing = speed / 110;
    for (let side = 0; side < 2; side++) {
      for (let k = 0; k < TRAIL; k++) {
        const v = (side * TRAIL + k) * 3;
        trailPos[v] = (history[k] ?? x) + (side ? 1.25 : -1.25);
        trailPos[v + 1] = ship.position.y - 0.05;
        trailPos[v + 2] = 0.75 + k * spacing;
      }
    }
    trailAttr.needsUpdate = true;

    // streaks
    const len = 0.2 + speed * 0.09;
    streaks.forEach((s, i) => {
      s.z += speed * dt;
      if (s.z > 10) s.z -= 170;
      streakPos.set([s.x, s.y, s.z, s.x, s.y, s.z - len], i * 6);
    });
    streakAttr.needsUpdate = true;

    // camera: follow the ship; leave room for the card when stopped on a wide screen
    const wantOffset = mode !== "run" && camera.aspect > 1.2 ? -3.4 : 0;
    camOffset += (wantOffset - camOffset) * Math.min(1, dt * 2.5);
    shake = Math.max(0, shake - dt);
    const jitter = reduced ? 0 : shake;
    camera.position.set(
      x * 0.55 + camOffset + (Math.random() - 0.5) * jitter,
      3.9 + (Math.random() - 0.5) * jitter,
      9.5,
    );
    camera.lookAt(x * 0.7 + camOffset * 0.6, 0.4, -16);
    const fov = 66 + speed * 0.35;
    if (Math.abs(camera.fov - fov) > 0.05) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }

    statsClock += dt;
    if (statsClock > 0.15) {
      statsClock = 0;
      opts.onStats({ distance: d, shards, hits });
    }
  }

  renderer.setAnimationLoop(() => {
    const now = performance.now();
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    step(reduced ? 0 : dt);
    composer.render();
  });

  return {
    launch() {
      if (next >= opts.gates) return;
      if (reduced) {
        d = stopAt();
        arrive();
        return;
      }
      mode = "run";
    },
    dispose() {
      renderer.setAnimationLoop(null);
      observer.disconnect();
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      for (const t of POINTER) el.removeEventListener(t, onPointer);
      scene.traverse((o) => {
        if (o instanceof THREE.Line || o instanceof THREE.Points) {
          (o.geometry as THREE.BufferGeometry).dispose();
          const m = o.material as THREE.Material | THREE.Material[];
          for (const mat of Array.isArray(m) ? m : [m]) mat.dispose();
        }
      });
      for (const g of [shardGeo, blockGeo]) g.dispose();
      for (const m of [shardMat, blockMat, hitMat]) m.dispose();
      composer.dispose();
      target.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
