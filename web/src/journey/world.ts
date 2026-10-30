/**
 * The onboarding world (three.js): an original low-poly meadow with a winding stone path,
 * one flag per tour stop, and a small sprout character that hops from stop to stop. Leaf tokens
 * along the path are collected as the sprout passes them.
 */
import * as THREE from "three";
import SpriteText from "three-spritetext";

export interface World {
  goTo(index: number): void;
  dispose(): void;
}

interface Options {
  stops: string[]; // flag labels
  onLeaf?: () => void; // a leaf token was collected
  reducedMotion?: boolean;
}

const COLORS = {
  sky: 0x0d2a24,
  fog: 0x0d2a24,
  grass: 0x2f7d4a,
  grassDark: 0x235f38,
  path: 0xc9b58a,
  trunk: 0x6b4a2b,
  leafA: 0x3fbf6a,
  leafB: 0x2e9e57,
  leafC: 0x7ed957,
  flag: 0xfacc15,
  flagDone: 0x4ade80,
  sprout: 0x9be36b,
  sproutDark: 0x5fae3f,
  token: 0xfde047,
  cloud: 0xe7f5ec,
  firefly: 0xfff6a8,
};

/** Deterministic random numbers so the world looks the same on every visit. */
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

function tree(rand: () => number): THREE.Group {
  const g = new THREE.Group();
  const trunkH = 0.6 + rand() * 0.5;
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.12, 0.16, trunkH, 6),
    new THREE.MeshStandardMaterial({ color: COLORS.trunk, flatShading: true }),
  );
  trunk.position.y = trunkH / 2;
  g.add(trunk);
  const colors = [COLORS.leafA, COLORS.leafB, COLORS.leafC];
  const layers = 2 + Math.floor(rand() * 2);
  for (let i = 0; i < layers; i++) {
    const cone = new THREE.Mesh(
      new THREE.ConeGeometry(0.9 - i * 0.22, 1.1, 7),
      new THREE.MeshStandardMaterial({
        color: colors[Math.floor(rand() * colors.length)],
        flatShading: true,
      }),
    );
    cone.position.y = trunkH + 0.4 + i * 0.55;
    g.add(cone);
  }
  const s = 0.8 + rand() * 0.9;
  g.scale.set(s, s, s);
  return g;
}

function cloud(rand: () => number): THREE.Group {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({
    color: COLORS.cloud,
    flatShading: true,
    transparent: true,
    opacity: 0.85,
  });
  const puffs = 3 + Math.floor(rand() * 3);
  for (let i = 0; i < puffs; i++) {
    const puff = new THREE.Mesh(new THREE.IcosahedronGeometry(0.7 + rand() * 0.6, 0), mat);
    puff.position.set(i * 0.8 - puffs * 0.4, rand() * 0.3, rand() * 0.4);
    g.add(puff);
  }
  return g;
}

/** The sprout: an original character, a round seedling with two leaves and a face. */
function sprout(): { group: THREE.Group; leaves: THREE.Group } {
  const group = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.42, 0.35, 6, 12),
    new THREE.MeshStandardMaterial({ color: COLORS.sprout, flatShading: true }),
  );
  body.position.y = 0.62;
  group.add(body);
  const eyeMat = new THREE.MeshStandardMaterial({ color: 0x0b1a12 });
  for (const x of [-0.15, 0.15]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.06, 8, 8), eyeMat);
    eye.position.set(x, 0.78, 0.38);
    group.add(eye);
  }
  const cheekMat = new THREE.MeshStandardMaterial({ color: 0xf2a7a7 });
  for (const x of [-0.26, 0.26]) {
    const cheek = new THREE.Mesh(new THREE.SphereGeometry(0.05, 8, 8), cheekMat);
    cheek.position.set(x, 0.66, 0.33);
    cheek.scale.set(1.3, 0.7, 0.5);
    group.add(cheek);
  }
  const leaves = new THREE.Group();
  const stem = new THREE.Mesh(
    new THREE.CylinderGeometry(0.03, 0.04, 0.3, 6),
    new THREE.MeshStandardMaterial({ color: COLORS.sproutDark }),
  );
  stem.position.y = 0.15;
  leaves.add(stem);
  for (const side of [-1, 1]) {
    const leaf = new THREE.Mesh(
      new THREE.SphereGeometry(0.2, 8, 6),
      new THREE.MeshStandardMaterial({ color: COLORS.leafC, flatShading: true }),
    );
    leaf.scale.set(1.4, 0.35, 0.7);
    leaf.position.set(side * 0.2, 0.32, 0);
    leaf.rotation.z = side * -0.5;
    leaves.add(leaf);
  }
  leaves.position.y = 1.08;
  group.add(leaves);
  return { group, leaves };
}

function flag(label: string, index: number): { group: THREE.Group; cloth: THREE.Mesh } {
  const group = new THREE.Group();
  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.05, 0.05, 2.2, 6),
    new THREE.MeshStandardMaterial({ color: 0xe5e7eb }),
  );
  pole.position.y = 1.1;
  group.add(pole);
  const cloth = new THREE.Mesh(
    new THREE.PlaneGeometry(0.9, 0.55, 8, 2),
    new THREE.MeshStandardMaterial({
      color: COLORS.flag,
      side: THREE.DoubleSide,
      flatShading: true,
    }),
  );
  cloth.position.set(0.47, 1.85, 0);
  group.add(cloth);
  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(0.75, 0.85, 0.12, 16),
    new THREE.MeshStandardMaterial({ color: 0x9ca3af, flatShading: true }),
  );
  base.position.y = 0.06;
  group.add(base);
  const text = new SpriteText(`${String(index + 1)} · ${label}`, 0.32, "#f0fdf4");
  text.fontFace = "Nunito Variable, Nunito, sans-serif";
  text.fontWeight = "800";
  text.backgroundColor = "rgba(6, 40, 24, 0.75)";
  text.padding = 0.08; // scene units, like the text height
  text.borderRadius = 0.08;
  text.position.y = 2.7;
  group.add(text);
  return { group, cloth };
}

export function createWorld(container: HTMLElement, options: Options): World | null {
  if (!webglAvailable()) return null;
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch {
    return null;
  }
  const reduced = options.reducedMotion ?? false;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(COLORS.sky);
  scene.fog = new THREE.Fog(COLORS.fog, 18, 55);
  scene.add(new THREE.HemisphereLight(0xd9f99d, 0x0f3d24, 1.1));
  const sun = new THREE.DirectionalLight(0xfff1c1, 1.6);
  sun.position.set(8, 14, 6);
  scene.add(sun);

  const camera = new THREE.PerspectiveCamera(
    50,
    container.clientWidth / container.clientHeight,
    0.1,
    200,
  );

  // ground with soft hills
  const ground = new THREE.PlaneGeometry(160, 60, 80, 30);
  const pos = ground.getAttribute("position");
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i);
    const y = pos.getY(i);
    pos.setZ(
      i,
      Math.sin(x * 0.18) * 0.35 +
        Math.cos(y * 0.25) * 0.3 +
        (Math.abs(y) > 8 ? (Math.abs(y) - 8) * 0.25 : 0),
    );
  }
  ground.computeVertexNormals();
  const groundMesh = new THREE.Mesh(
    ground,
    new THREE.MeshStandardMaterial({ color: COLORS.grass, flatShading: true }),
  );
  groundMesh.rotation.x = -Math.PI / 2;
  groundMesh.position.y = -0.2;
  scene.add(groundMesh);

  // the path: a winding curve through one point per stop
  const spacing = 9;
  const points = options.stops.map(
    (_, i) => new THREE.Vector3(i * spacing, 0, Math.sin(i * 1.3) * 2.4),
  );
  points.unshift(new THREE.Vector3(-spacing * 0.6, 0, 0));
  const curve = new THREE.CatmullRomCurve3(points);
  const stoneMat = new THREE.MeshStandardMaterial({ color: COLORS.path, flatShading: true });
  const stones = Math.floor(curve.getLength() / 0.9);
  const rand = rng(7);
  for (let i = 0; i <= stones; i++) {
    const p = curve.getPointAt(i / stones);
    const stone = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.48, 0.1, 7), stoneMat);
    stone.position.set(p.x + (rand() - 0.5) * 0.2, 0.02, p.z + (rand() - 0.5) * 0.2);
    stone.rotation.y = rand() * Math.PI;
    scene.add(stone);
  }
  // where each stop sits along the curve (0..1)
  const stopT = options.stops.map((_, i) => (i + 1) / options.stops.length);

  const flags = options.stops.map((label, i) => {
    const f = flag(label, i);
    const p = curve.getPointAt(stopT[i] ?? 1);
    f.group.position.set(p.x + 1.1, 0, p.z - 1.2);
    scene.add(f.group);
    return f;
  });

  // a forest behind the path; only low bushes between the path and the camera, so nothing
  // tall ever blocks the view
  const span = spacing * options.stops.length + 24;
  for (let i = 0; i < 110; i++) {
    const t = tree(rand);
    t.position.set(-14 + rand() * span, 0, -4.5 - rand() * 20);
    scene.add(t);
  }
  for (let i = 0; i < 40; i++) {
    const bush = tree(rand);
    bush.scale.multiplyScalar(0.32);
    bush.position.set(-14 + rand() * span, 0, 3.2 + rand() * 2.2);
    scene.add(bush);
  }
  const clouds: THREE.Group[] = [];
  for (let i = 0; i < 10; i++) {
    const c = cloud(rand);
    c.position.set(rand() * spacing * options.stops.length, 11 + rand() * 4, -26 + rand() * 8);
    scene.add(c);
    clouds.push(c);
  }

  // leaf tokens between stops
  const tokenGeo = new THREE.OctahedronGeometry(0.22, 0);
  const tokenMat = new THREE.MeshStandardMaterial({
    color: COLORS.token,
    emissive: 0x854d0e,
    flatShading: true,
  });
  const tokens: { mesh: THREE.Mesh; t: number; taken: boolean }[] = [];
  for (let i = 0; i < options.stops.length * 3; i++) {
    const t = 0.04 + (i / (options.stops.length * 3)) * 0.95;
    if (stopT.some((s) => Math.abs(s - t) < 0.03)) continue;
    const p = curve.getPointAt(t);
    const mesh = new THREE.Mesh(tokenGeo, tokenMat);
    mesh.position.set(p.x, 0.9, p.z);
    scene.add(mesh);
    tokens.push({ mesh, t, taken: false });
  }

  // fireflies
  const flyCount = 140;
  const flyPositions = new Float32Array(flyCount * 3);
  for (let i = 0; i < flyCount; i++) {
    flyPositions[i * 3] = -10 + rand() * (spacing * options.stops.length + 20);
    flyPositions[i * 3 + 1] = 0.5 + rand() * 4;
    flyPositions[i * 3 + 2] = -12 + rand() * 24;
  }
  const flyGeo = new THREE.BufferGeometry();
  flyGeo.setAttribute("position", new THREE.BufferAttribute(flyPositions, 3));
  const fireflies = new THREE.Points(
    flyGeo,
    new THREE.PointsMaterial({
      color: COLORS.firefly,
      size: 0.09,
      transparent: true,
      opacity: 0.8,
    }),
  );
  scene.add(fireflies);

  const hero = sprout();
  scene.add(hero.group);

  let current = 0; // curve position of the sprout (0..1)
  let target = 0;
  let from = 0;
  let hopStart = 0;
  let hopDuration = 0;
  const start = curve.getPointAt(0);
  hero.group.position.copy(start);
  const camPos = new THREE.Vector3(start.x - 4, 4.5, start.z + 9);
  camera.position.copy(camPos);

  const started = performance.now();
  let frame = 0;
  let disposed = false;

  function render() {
    if (disposed) return;
    frame = requestAnimationFrame(render);
    const now = (performance.now() - started) / 1000;
    let height = 0;
    if (hopDuration > 0) {
      const k = Math.min(1, (now - hopStart) / hopDuration);
      const eased = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      current = from + (target - from) * eased;
      // a few small hops on the way, platformer style
      const hops = Math.max(1, Math.round(Math.abs(target - from) * 14));
      height = Math.abs(Math.sin(k * Math.PI * hops)) * 0.6;
      if (k >= 1) hopDuration = 0;
    }
    const p = curve.getPointAt(Math.min(1, Math.max(0, current)));
    const ahead = curve.getPointAt(Math.min(1, current + 0.01));
    hero.group.position.set(p.x, p.y + height, p.z);
    hero.group.lookAt(ahead.x, hero.group.position.y, ahead.z); // the face (+z) looks down the path
    const breathe = reduced ? 0 : Math.sin(now * 3) * 0.04;
    const size = 1.15;
    hero.group.scale.set(
      size * (1 + breathe * 0.5),
      size * (1 - breathe),
      size * (1 + breathe * 0.5),
    );
    hero.leaves.rotation.z = reduced ? 0 : Math.sin(now * 2.2) * 0.15;

    for (const token of tokens) {
      if (!token.taken && current >= token.t) {
        token.taken = true;
        options.onLeaf?.();
      }
      if (token.taken) {
        token.mesh.position.y += 0.08;
        token.mesh.scale.multiplyScalar(0.93);
        token.mesh.visible = token.mesh.scale.x > 0.05;
      } else if (!reduced) {
        token.mesh.rotation.y = now * 2;
        token.mesh.position.y = 0.9 + Math.sin(now * 2 + token.t * 40) * 0.08;
      }
    }
    flags.forEach((f, i) => {
      const reached = current >= (stopT[i] ?? 1) - 0.005;
      (f.cloth.material as THREE.MeshStandardMaterial).color.setHex(
        reached ? COLORS.flagDone : COLORS.flag,
      );
      if (!reduced) {
        const arr = f.cloth.geometry.getAttribute("position");
        for (let v = 0; v < arr.count; v++) {
          const x = arr.getX(v);
          arr.setZ(v, Math.sin(now * 4 + x * 5 + i) * 0.06 * (x + 0.45));
        }
        arr.needsUpdate = true;
      }
    });
    if (!reduced) {
      for (const c of clouds) c.position.x += 0.004;
      fireflies.rotation.y = Math.sin(now * 0.1) * 0.02;
      fireflies.material.opacity = 0.55 + Math.sin(now * 1.7) * 0.25;
    }
    // camera follows from the side and slightly behind, like a side-scroller; it aims below
    // the sprout so the sprout sits in the upper half, clear of the info card
    const desired = new THREE.Vector3(p.x - 3.5, 5, p.z + 11);
    camPos.lerp(desired, reduced ? 1 : 0.06);
    camera.position.copy(camPos);
    camera.lookAt(p.x + 2.5, -0.4, p.z);
    renderer.render(scene, camera);
  }
  render();

  function resize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);

  return {
    goTo(index: number) {
      const next = index < 0 ? 0 : (stopT[Math.min(index, stopT.length - 1)] ?? 1);
      from = current;
      target = next;
      hopStart = (performance.now() - started) / 1000;
      hopDuration = reduced ? 0.01 : Math.min(2.4, 0.6 + Math.abs(next - current) * 6);
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh || obj instanceof THREE.Points) {
          (obj.geometry as THREE.BufferGeometry).dispose();
          const material = obj.material as THREE.Material | THREE.Material[];
          for (const m of Array.isArray(material) ? material : [material]) m.dispose();
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
