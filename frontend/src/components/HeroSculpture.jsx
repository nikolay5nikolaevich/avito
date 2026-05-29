import { useMemo, useEffect, useRef } from "react";
import "./HeroSculpture.css";

// ─── HeroSculpture ────────────────────────────────────────────────────────────
// Параметрический torus knot (p=3, q=2) — трилистниковый узел.
// Отрисован «трубой из бусин» с painter's-сортировкой по глубине:
// дальние бусины рисуются первыми → ближние их перекрывают → настоящее плетение.
// Затенение: lerp от графита (#1f1f24) через нейтральный стальной (#9a9aa3) к bone (#f6f4ee).
// Ice-ребро: мягкая холодная подсветка по верхне-левому краю (пониженная opacity + малый радиус).
// Glow: тонкий ambient-ореол, stdDeviation снижен, opacity слоя снижена.
// ─────────────────────────────────────────────────────────────────────────────

// ── Вспомогательная: линейная интерполяция числа ─────────────────────────────
function lerp(a, b, t) {
  return a + (b - a) * t;
}

// ── Интерполяция цвета (hex) по трём опорным точкам ──────────────────────────
// t ∈ [0..1]: 0 = дальний тёмный, 1 = ближний светлый
function depthColor(t) {
  // Опорные точки в RGB
  // far — тёмный графит без синевы; mid — нейтральный стальной без синевы; near — ярко-bone
  const far  = [31,  31,  36];   // #1f1f24 — дальний графит
  const mid  = [154, 154, 163];  // #9a9aa3 — нейтрально-стальной (нет синего уклона)
  const near = [246, 244, 238];  // #f6f4ee — ближний bone/почти белый

  let r, g, b;
  if (t < 0.5) {
    const u = t * 2; // [0..1] на отрезке far→mid
    r = Math.round(lerp(far[0], mid[0], u));
    g = Math.round(lerp(far[1], mid[1], u));
    b = Math.round(lerp(far[2], mid[2], u));
  } else {
    const u = (t - 0.5) * 2; // [0..1] на отрезке mid→near
    r = Math.round(lerp(mid[0], near[0], u));
    g = Math.round(lerp(mid[1], near[1], u));
    b = Math.round(lerp(mid[2], near[2], u));
  }
  return `rgb(${r},${g},${b})`;
}

// ── Интерполяция цвета ice-блика ──────────────────────────────────────────────
// t ∈ [0..1]: ice-градиент от #cfeaff → #9fd2ff → #5aa8e6
function iceColor(t) {
  const c0 = [207, 234, 255]; // #cfeaff
  const c1 = [159, 210, 255]; // #9fd2ff
  const c2 = [90,  168, 230]; // #5aa8e6
  let r, g, b;
  if (t < 0.5) {
    const u = t * 2;
    r = Math.round(lerp(c0[0], c1[0], u));
    g = Math.round(lerp(c0[1], c1[1], u));
    b = Math.round(lerp(c0[2], c1[2], u));
  } else {
    const u = (t - 0.5) * 2;
    r = Math.round(lerp(c1[0], c2[0], u));
    g = Math.round(lerp(c1[1], c2[1], u));
    b = Math.round(lerp(c1[2], c2[2], u));
  }
  return `rgb(${r},${g},${b})`;
}

// ── Генерация геометрии torus knot ───────────────────────────────────────────
// p=3, q=2 → трилистниковый узел (trefoil knot)
// R=2 — большой радиус тора, r=1 — малый
// Наклон: вокруг X на ax, вокруг Y на ay
const N_POINTS  = 720;    // число семплов
const VIEWBOX   = 400;    // размер SVG viewBox
const PADDING   = 0.14;   // отступ от краёв (14%)
const BASE_R    = VIEWBOX * 0.058; // базовый радиус бусины (+12% для весомости формы)
const PERSP_AMP = 0.28;   // амплитуда перспективного увеличения

function generateKnotPoints() {
  const p = 3, q = 2, R = 2, r = 1;
  const ax = -0.95; // наклон вокруг X (рад)
  const ay =  0.50; // наклон вокруг Y (рад)

  const cosAx = Math.cos(ax), sinAx = Math.sin(ax);
  const cosAy = Math.cos(ay), sinAy = Math.sin(ay);

  const raw = [];
  for (let i = 0; i < N_POINTS; i++) {
    const t  = (i / N_POINTS) * 2 * Math.PI;
    // Torus knot в локальных координатах
    const cx = Math.cos(p * t) * (R + r * Math.cos(q * t));
    const cy = Math.sin(p * t) * (R + r * Math.cos(q * t));
    const cz = r * Math.sin(q * t);

    // Поворот вокруг Y
    const x1 =  cx * cosAy + cz * sinAy;
    const z1 = -cx * sinAy + cz * cosAy;
    const y1 =  cy;

    // Поворот вокруг X
    const x2 = x1;
    const y2 = y1 * cosAx - z1 * sinAx;
    const z2 = y1 * sinAx + z1 * cosAx;

    raw.push({ x: x2, y: y2, z: z2 });
  }

  // Нормализация в viewBox с отступом
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;
  let minZ = Infinity, maxZ = -Infinity;
  for (const p of raw) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
    if (p.z < minZ) minZ = p.z;
    if (p.z > maxZ) maxZ = p.z;
  }

  const rangeX = maxX - minX;
  const rangeY = maxY - minY;
  const scale  = Math.max(rangeX, rangeY); // сохраняем aspect ratio
  const pad    = VIEWBOX * PADDING;
  const usable = VIEWBOX - 2 * pad;

  const points = raw.map((p) => {
    const sx = pad + ((p.x - minX) / scale) * usable;
    const sy = pad + ((p.y - minY) / scale) * usable;
    // Центрируем по оси с меньшим диапазоном
    const offX = rangeX < scale ? ((scale - rangeX) / scale) * usable * 0.5 : 0;
    const offY = rangeY < scale ? ((scale - rangeY) / scale) * usable * 0.5 : 0;
    // depth нормализован [0..1]: 0 = дальний, 1 = ближний
    const depthNorm = (p.z - minZ) / (maxZ - minZ);
    return { x: sx + offX, y: sy + offY, depth: depthNorm, idx: 0 };
  });

  // Сортировка по depth возрастанию (дальние первыми → painter's algorithm)
  const sorted = points
    .map((pt, i) => ({ ...pt, idx: i }))
    .sort((a, b) => a.depth - b.depth);

  return sorted;
}

// ─────────────────────────────────────────────────────────────────────────────

export default function HeroSculpture() {
  // Геометрия считается ОДИН раз при монтировании
  const points = useMemo(() => generateKnotPoints(), []);

  const parallaxRef = useRef(null);
  const rafRef      = useRef(null);
  const targetRef   = useRef({ x: 0, y: 0 });
  const currentRef  = useRef({ x: 0, y: 0 });

  useEffect(() => {
    // Уважаем prefers-reduced-motion
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    if (reducedMotion) return;

    const AMPLITUDE = 6;   // максимальный сдвиг px
    const LERP_K    = 0.06; // коэффициент сглаживания

    function onPointerMove(e) {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const nx = (e.clientX / vw - 0.5) * 2;
      const ny = (e.clientY / vh - 0.5) * 2;
      targetRef.current = { x: nx * AMPLITUDE, y: ny * AMPLITUDE };
    }

    function loop() {
      const t = targetRef.current;
      const c = currentRef.current;
      c.x += (t.x - c.x) * LERP_K;
      c.y += (t.y - c.y) * LERP_K;
      if (parallaxRef.current) {
        parallaxRef.current.style.transform =
          `translate(${c.x.toFixed(2)}px, ${c.y.toFixed(2)}px)`;
      }
      rafRef.current = requestAnimationFrame(loop);
    }

    window.addEventListener("pointermove", onPointerMove, { passive: true });
    rafRef.current = requestAnimationFrame(loop);

    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // ── Построение SVG-элементов ────────────────────────────────────────────────
  // Слой 1: glow-дубликат (размытый, позади трубы)
  // Слой 2: труба из бусин (depth-sorted)
  // Слой 3: ice-ребро (только ближние точки)

  // Glow-дубликат: тонкий ambient-ореол; opacity снижена чтобы узел не тонул в синеве
  const glowCircles = points
    // Берём каждую 4-ю точку — реже = меньше наложения = тоньше ореол
    .filter((_, i) => i % 4 === 0)
    .map((pt) => {
      const r = BASE_R * (1 + PERSP_AMP * pt.depth) * 1.4;
      return (
        <circle
          key={`g${pt.idx}`}
          cx={pt.x.toFixed(2)}
          cy={pt.y.toFixed(2)}
          r={r.toFixed(2)}
          fill="rgba(159,210,255,0.11)"
        />
      );
    });

  const tubeCircles = points.map((pt) => {
    const r     = BASE_R * (1 + PERSP_AMP * pt.depth);
    const color = depthColor(pt.depth);
    return (
      <circle
        key={`t${pt.idx}`}
        cx={pt.x.toFixed(2)}
        cy={pt.y.toFixed(2)}
        r={r.toFixed(2)}
        fill={color}
      />
    );
  });

  // Ice-ребро: гладкая холодная подсветка по верхне-левому краю трубы.
  // Все точки (без прореживания) + малый радиус + низкая opacity → непрерывная линия, не точки.
  const iceCircles = points
    .filter((pt) => pt.depth > 0.55)
    .map((pt) => {
      const tubeR  = BASE_R * (1 + PERSP_AMP * pt.depth);
      // Тонкая бусина — 22% радиуса трубы; непрерывно перекрываются → гладкий блик
      const iceR   = tubeR * 0.22;
      // Смещение вверх-влево на 28% радиуса трубы
      const offX   = -tubeR * 0.28;
      const offY   = -tubeR * 0.28;
      const color  = iceColor((pt.depth - 0.55) / 0.45); // нормируем [0.55..1] → [0..1]
      return (
        <circle
          key={`i${pt.idx}`}
          cx={(pt.x + offX).toFixed(2)}
          cy={(pt.y + offY).toFixed(2)}
          r={iceR.toFixed(2)}
          fill={color}
          opacity="0.34"
        />
      );
    });

  return (
    <div className="hs-root">
      {/* Фоновый glow-ореол */}
      <div className="hs-glow-bg" />

      {/* Параллакс-обёртка */}
      <div className="hs-parallax" ref={parallaxRef}>
        <svg
          className="hs-svg"
          viewBox="0 0 400 400"
          xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Трилистниковый узел"
        >
          <defs>
            {/* Фильтр мягкого размытия для glow-слоя; stdDeviation снижен для тонкого ореола */}
            <filter id="hs-glow-blur" x="-25%" y="-25%" width="150%" height="150%">
              <feGaussianBlur stdDeviation="3.5" />
            </filter>
          </defs>

          {/* ── Покачивающаяся группа ────────────────────────────────────── */}
          <g className="hs-sway" style={{ transformOrigin: "200px 200px" }}>

            {/* Слой 1: размытый glow-дубликат позади трубы */}
            <g filter="url(#hs-glow-blur)" className="hs-glow-pulse">
              {glowCircles}
            </g>

            {/* Слой 2: труба из бусин с depth-сортировкой и затенением */}
            {tubeCircles}

            {/* Слой 3: ice-ребро на ближних/освещённых точках */}
            {iceCircles}

          </g>
        </svg>
      </div>
    </div>
  );
}
