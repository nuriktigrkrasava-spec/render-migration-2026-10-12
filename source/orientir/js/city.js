import * as THREE from 'three';

/* Ночной макет города: пять башен на общей платформе.
   Этаж — задача, тренировка или доля накопления; заполненные светятся. */

const SPACING = 3.7;
const SLAB_W = 1.5;
const SLOTS = 14;          /* каркас башни: столько этажей у пустой сферы */
const SLAB_H = 0.36;
const MAX_TOWER_HEIGHT = 11;

let renderer, scene, camera, raycaster, labelBox;
let skyMat, platform, hemi, sun;
let towers = [];
let ready = false;
let lastData = null;
let lastSelected = null;
let lastPanel = false;

const THEMES = {
  light: {
    skyHigh: '#cfe0f5', skyLow: '#f5f8fc', fog: 0xeff3f9,
    platform: 0xdde4ee, plinth: 0xc7d0de,
    ghost: 0x8e9cb5, ghostOpacity: 0.16, edge: 0x6d7a95, edgeOpacity: 0.34,
    emissive: 0.05, roughness: 0.5, glow: 0.15,
    hemiSky: 0xffffff, hemiGround: 0xc3cddc, hemi: 2.6,
    sun: 0xfff4e6, sunPower: 3.0, exposure: 1.0
  },
  dark: {
    skyHigh: '#1d2740', skyLow: '#05070d', fog: 0x080b13,
    platform: 0x141c2c, plinth: 0x1b2436,
    ghost: 0xb6c6e2, ghostOpacity: 0.035, edge: 0xb9cbe8, edgeOpacity: 0.22,
    emissive: 0.26, roughness: 0.66, glow: 1,
    hemiSky: 0x33456b, hemiGround: 0x080c15, hemi: 1.15,
    sun: 0xdfe8ff, sunPower: 2.1, exposure: 1.05
  }
};

function theme() {
  return THEMES[document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'];
}

const view = {
  radius: 20, theta: -0.3, phi: 1.19,
  targetRadius: 20, targetTheta: -0.3, targetPhi: 1.19,
  focusX: 0, targetFocusX: 0,
  chosen: false,
  height: 2.4, targetHeight: 2.4,
  spin: true
};

const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ── геометрия ────────────────────────────────────────────────── */

function roundedRect(w, d, r) {
  const shape = new THREE.Shape();
  const x = -w / 2;
  const y = -d / 2;
  shape.moveTo(x + r, y);
  shape.lineTo(x + w - r, y);
  shape.quadraticCurveTo(x + w, y, x + w, y + r);
  shape.lineTo(x + w, y + d - r);
  shape.quadraticCurveTo(x + w, y + d, x + w - r, y + d);
  shape.lineTo(x + r, y + d);
  shape.quadraticCurveTo(x, y + d, x, y + d - r);
  shape.lineTo(x, y + r);
  shape.quadraticCurveTo(x, y, x + r, y);
  return shape;
}

function slab(w, h, d, r) {
  const bevel = Math.min(0.05, h * 0.28);
  const geo = new THREE.ExtrudeGeometry(roundedRect(w, d, r), {
    depth: Math.max(0.002, h - bevel * 2),
    bevelEnabled: true,
    bevelSize: bevel,
    bevelThickness: bevel,
    bevelSegments: 2,
    curveSegments: 8
  });
  geo.rotateX(-Math.PI / 2);
  geo.computeBoundingBox();
  geo.translate(0, -geo.boundingBox.min.y, 0);
  return geo;
}

/* ── небо ─────────────────────────────────────────────────────── */

function sky() {
  const geo = new THREE.SphereGeometry(140, 32, 24);
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      high: { value: new THREE.Color(theme().skyHigh) },
      low: { value: new THREE.Color(theme().skyLow) }
    },
    vertexShader: `
      varying float h;
      void main() {
        h = normalize(position).y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform vec3 high;
      uniform vec3 low;
      varying float h;
      void main() {
        float t = smoothstep(-0.15, 0.75, h);
        gl_FragColor = vec4(mix(low, high, t), 1.0);
      }`
  });
  skyMat = mat;
  return new THREE.Mesh(geo, mat);
}


/* ── сборка сцены ─────────────────────────────────────────────── */

function build(spheres) {
  const canvas = document.getElementById('scene');
  renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = theme().exposure;

  scene = new THREE.Scene();
  scene.fog = new THREE.Fog(theme().fog, 34, 92);
  scene.add(sky());

  camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 300);
  raycaster = new THREE.Raycaster();
  labelBox = document.getElementById('labels');

  hemi = new THREE.HemisphereLight(theme().hemiSky, theme().hemiGround, theme().hemi);
  scene.add(hemi);

  sun = new THREE.DirectionalLight(theme().sun, theme().sunPower);
  sun.position.set(-14, 22, 12);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -22;
  sun.shadow.camera.right = 22;
  sun.shadow.camera.top = 20;
  sun.shadow.camera.bottom = -12;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 70;
  sun.shadow.bias = -0.0006;
  sun.shadow.normalBias = 0.02;
  scene.add(sun);

  const width = SPACING * spheres.length + 2.6;
  platform = new THREE.Mesh(
    slab(width, 1.0, 7.6, 1.2),
    new THREE.MeshStandardMaterial({ color: theme().platform, roughness: 0.5, metalness: 0.3 })
  );
  platform.position.y = -1.0;
  platform.receiveShadow = true;
  scene.add(platform);

  spheres.forEach((sphere, index) => {
    const group = new THREE.Group();
    group.position.x = (index - (spheres.length - 1) / 2) * SPACING;
    group.position.z = -Math.abs(index - (spheres.length - 1) / 2) * 0.45;
    scene.add(group);

    const color = new THREE.Color(sphere.color);

    const plinth = new THREE.Mesh(
      slab(SLAB_W + 0.4, 0.26, SLAB_W + 0.4, 0.16),
      new THREE.MeshStandardMaterial({ color: theme().plinth, roughness: 0.6, metalness: 0.25 })
    );
    plinth.receiveShadow = true;
    plinth.castShadow = true;
    group.add(plinth);

    const glow = new THREE.PointLight(color, 0, 13, 2);
    glow.position.y = 1;
    group.add(glow);

    const tag = document.createElement('div');
    tag.className = 'tag';
    tag.style.setProperty('--tag-color', sphere.color);
    const name = document.createElement('b');
    name.textContent = sphere.name;
    const count = document.createElement('span');
    tag.appendChild(name);
    tag.appendChild(count);
    labelBox.appendChild(tag);

    towers.push({
      sphere: sphere,
      plinth: plinth,
      color: color,
      group: group,
      glow: glow,
      tag: tag,
      count: count,
      floors: [],
      state: null,
      height: 0.26,
      dim: 1,
      targetDim: 1,
      glowFull: 0
    });
  });
}

/* ── башни ────────────────────────────────────────────────────── */

function rebuild(tower, lit, slots) {
  for (const floor of tower.floors) {
    tower.group.remove(floor.mesh);
    floor.mesh.geometry.dispose();
    floor.mesh.material.dispose();
    if (floor.edges) {
      tower.group.remove(floor.edges);
      floor.edges.geometry.dispose();
      floor.edges.material.dispose();
    }
  }
  tower.floors = [];

  const height = Math.min(SLAB_H, MAX_TOWER_HEIGHT / Math.max(slots, 1));
  const step = height * 1.16;
  const geo = slab(SLAB_W, height, SLAB_W, 0.12);
  const base = 0.26;
  const look = theme();

  for (let i = 0; i < slots; i += 1) {
    const on = i < lit;
    const material = on
      ? new THREE.MeshStandardMaterial({
          color: tower.color,
          emissive: tower.color,
          emissiveIntensity: look.emissive,
          roughness: look.roughness,
          metalness: 0.05
        })
      : new THREE.MeshStandardMaterial({
          color: look.ghost,
          roughness: 0.95,
          metalness: 0,
          transparent: true,
          opacity: look.ghostOpacity
        });

    const mesh = new THREE.Mesh(geo.clone(), material);
    mesh.position.y = base + i * step;
    mesh.castShadow = on;
    mesh.receiveShadow = on;
    mesh.userData.tower = tower;
    mesh.scale.y = still ? 1 : 0.02;
    tower.group.add(mesh);

    const floor = { mesh: mesh, lit: on };

    if (!on) {
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(mesh.geometry, 25),
        new THREE.LineBasicMaterial({ color: look.edge, transparent: true, opacity: look.edgeOpacity })
      );
      edges.position.copy(mesh.position);
      edges.scale.copy(mesh.scale);
      tower.group.add(edges);
      floor.edges = edges;
    }

    tower.floors.push(floor);
  }

  geo.dispose();
  tower.height = base + slots * step;
  tower.glow.position.y = Math.max(0.9, lit * step + 0.7);
  tower.glowFull = (lit === 0 ? 0 : 3 + Math.min(lit, 20) * 0.7) * look.glow;
  tower.glow.intensity = tower.glowFull * tower.dim;
}

/* сколько этажей горит: у задач и накоплений с целью — доля от каркаса,
   у зала и накоплений без цели башня растёт этаж за этаж */
function shape(data, sphere) {
  const count = window.Stats.fill(data, sphere.id);

  if (sphere.kind === 'gym' || (sphere.kind === 'money' && data.money.goal <= 0)) {
    return { lit: count.filled, slots: Math.max(count.filled, SLOTS) };
  }

  if (count.total === 0) return { lit: 0, slots: SLOTS };
  return { lit: Math.round(count.filled / count.total * SLOTS), slots: SLOTS };
}

export function update(data, selectedId, panelOpen) {
  if (!ready) return;
  lastData = data;
  lastSelected = selectedId;
  lastPanel = !!panelOpen;

  for (const tower of towers) {
    const form = shape(data, tower.sphere);
    const key = form.lit + '/' + form.slots;
    if (key !== tower.state) {
      rebuild(tower, form.lit, form.slots);
      tower.state = key;
    }
    tower.count.textContent = window.Stats.label(data, tower.sphere.id);
    tower.targetDim = (!selectedId || selectedId === tower.sphere.id) ? 1 : 0.32;
  }

  const chosen = towers.find(t => t.sphere.id === selectedId);
  /* при открытой панели смотрим правее башни: она уходит в левую половину экрана */
  view.targetFocusX = chosen ? chosen.group.position.x + 2.4 : 0;
  view.chosen = !!chosen;
  view.targetRadius = distance();
  view.targetHeight = chosen ? 3.4 : 2.4;
  view.spin = !selectedId;
}

export function setTheme() {
  if (!ready) return;
  const look = theme();

  skyMat.uniforms.high.value.set(look.skyHigh);
  skyMat.uniforms.low.value.set(look.skyLow);
  scene.fog.color.set(look.fog);
  renderer.toneMappingExposure = look.exposure;

  platform.material.color.set(look.platform);
  hemi.color.set(look.hemiSky);
  hemi.groundColor.set(look.hemiGround);
  hemi.intensity = look.hemi;
  sun.color.set(look.sun);
  sun.intensity = look.sunPower;

  for (const tower of towers) {
    tower.plinth.material.color.set(look.plinth);
    tower.state = null;          /* этажи пересоберутся с новыми материалами */
  }

  if (lastData) update(lastData, lastSelected, lastPanel);
}

/* ── управление камерой ───────────────────────────────────────── */

function control(canvas) {
  let dragging = false;
  let moved = 0;
  let lastX = 0;
  let lastY = 0;

  canvas.addEventListener('pointerdown', ev => {
    dragging = true;
    moved = 0;
    lastX = ev.clientX;
    lastY = ev.clientY;
    canvas.setPointerCapture(ev.pointerId);
    view.spin = false;
  });

  canvas.addEventListener('pointermove', ev => {
    if (!dragging) return;
    const dx = ev.clientX - lastX;
    const dy = ev.clientY - lastY;
    lastX = ev.clientX;
    lastY = ev.clientY;
    moved += Math.abs(dx) + Math.abs(dy);
    view.targetTheta -= dx * 0.005;
    view.targetPhi = clamp(view.targetPhi - dy * 0.004, 0.45, 1.44);
  });

  canvas.addEventListener('pointerup', ev => {
    dragging = false;
    if (moved < 6) pick(ev);
  });

  canvas.addEventListener('pointercancel', () => { dragging = false; });

  canvas.addEventListener('wheel', ev => {
    ev.preventDefault();
    view.targetRadius = clamp(view.targetRadius + ev.deltaY * 0.02, 12, 46);
  }, { passive: false });
}

function pick(ev) {
  const point = new THREE.Vector2(
    (ev.clientX / window.innerWidth) * 2 - 1,
    -(ev.clientY / window.innerHeight) * 2 + 1
  );
  raycaster.setFromCamera(point, camera);

  const meshes = [];
  for (const tower of towers) {
    meshes.push(tower.group.children.filter(c => c.isMesh));
  }
  const hits = raycaster.intersectObjects(meshes.flat(), false);

  if (hits.length === 0) { window.UI.close(); return; }

  let node = hits[0].object;
  while (node && !node.userData.tower) node = node.parent;
  const tower = node ? node.userData.tower : towers.find(t => t.group === hits[0].object.parent);
  if (tower) window.UI.open(tower.sphere.id);
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

/* ── кадр ─────────────────────────────────────────────────────── */

/* узкому окну нужен более дальний и фронтальный кадр,
   иначе пять башен выстраиваются друг за другом */
function distance() {
  const width = window.innerWidth;
  const k = width < 760 ? 2.3 : (width < 1100 ? 1.3 : 1);
  return (view.chosen ? 18 : 20) * k;
}

function resize() {
  view.targetRadius = distance();
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
}

const target = new THREE.Vector3();
const top = new THREE.Vector3();

function frame() {
  requestAnimationFrame(frame);

  if (view.spin && !still) view.targetTheta += 0.0011;

  const ease = still ? 1 : 0.09;
  view.theta += (view.targetTheta - view.theta) * ease;
  view.phi += (view.targetPhi - view.phi) * ease;
  view.radius += (view.targetRadius - view.radius) * ease;
  view.focusX += (view.targetFocusX - view.focusX) * ease;
  view.height += (view.targetHeight - view.height) * ease;

  target.set(view.focusX, view.height, 0);
  camera.position.set(
    target.x + view.radius * Math.sin(view.phi) * Math.sin(view.theta),
    target.y + view.radius * Math.cos(view.phi),
    target.z + view.radius * Math.sin(view.phi) * Math.cos(view.theta)
  );
  camera.lookAt(target);

  const look = theme();

  for (const tower of towers) {
    tower.dim += (tower.targetDim - tower.dim) * (still ? 1 : 0.08);
    tower.glow.intensity = tower.glowFull * tower.dim;

    for (const floor of tower.floors) {
      if (floor.mesh.scale.y < 1) {
        floor.mesh.scale.y = Math.min(1, floor.mesh.scale.y + 0.08);
        if (floor.edges) floor.edges.scale.y = floor.mesh.scale.y;
      }
      if (floor.lit) {
        floor.mesh.material.emissiveIntensity = look.emissive * tower.dim;
      } else {
        floor.mesh.material.opacity = look.ghostOpacity * tower.dim;
        if (floor.edges) floor.edges.material.opacity = look.edgeOpacity * tower.dim;
      }
    }

    top.set(tower.group.position.x, tower.height + 0.9, tower.group.position.z);
    top.project(camera);
    const x = (top.x * 0.5 + 0.5) * window.innerWidth;
    const y = (-top.y * 0.5 + 0.5) * window.innerHeight;
    tower.tag.style.transform = 'translate(-50%, -100%) translate(' + x.toFixed(1) + 'px, ' + y.toFixed(1) + 'px)';
    tower.tag.style.left = '0';
    tower.tag.style.top = '0';
    tower.tag.style.opacity = String(0.25 + 0.75 * tower.dim);
  }

  renderer.render(scene, camera);
}

export function start() {
  try {
    build(window.Model.SPHERES);
    control(document.getElementById('scene'));
    if (window.innerWidth < 760) {
      view.theta = view.targetTheta = -0.12;
      view.phi = view.targetPhi = 1.24;
    }
    view.radius = view.targetRadius = distance();
    window.addEventListener('resize', resize);
    ready = true;
    window.City = { update: update, setTheme: setTheme };
    /* модуль выполняется раньше DOMContentLoaded, данные может ещё не подняться:
       тогда первый кадр нарисует UI.render(), он сам зовёт City.update */
    const data = window.UI.data();
    if (data) update(data, window.UI.selected(), false);
    frame();
  } catch (err) {
    /* без сцены страница остаётся рабочей: сферы открываются кнопками внизу */
    console.error('Сцена не собралась:', err);
  }
}
