import { GestureAnimation } from "./types";

/** MediaPipe Hands landmark connections (bones) for a 21-point hand. */
export const HAND_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [1, 2], [2, 3], [3, 4], // thumb
  [0, 5], [5, 6], [6, 7], [7, 8], // index
  [5, 9], [9, 10], [10, 11], [11, 12], // middle
  [9, 13], [13, 14], [14, 15], [15, 16], // ring
  [13, 17], [17, 18], [18, 19], [19, 20], // pinky
  [0, 17], // palm edge
];

export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** Bounding box over every landmark in every frame, padded 10%. */
export function fitBounds(frames: [number, number, number][][]): Bounds {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const frame of frames) {
    for (const [x, y] of frame) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (!Number.isFinite(minX)) {
    return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  }
  const padX = (maxX - minX || 1) * 0.1;
  const padY = (maxY - minY || 1) * 0.1;
  return {
    minX: minX - padX,
    maxX: maxX + padX,
    minY: minY - padY,
    maxY: maxY + padY,
  };
}

/** Index of the animation frame active at elapsed seconds (looping). */
export function frameIndexAt(timestamps: number[], elapsed: number): number {
  if (timestamps.length === 0) {
    return 0;
  }
  const start = timestamps[0];
  const duration = timestamps[timestamps.length - 1] - start;
  if (duration <= 0) {
    return 0;
  }
  const t = start + (((elapsed % duration) + duration) % duration);
  let index = 0;
  while (index + 1 < timestamps.length && timestamps[index + 1] <= t) {
    index += 1;
  }
  return index;
}

export interface DrawStyle {
  bone: string;
  joint: string;
  lineWidth: number;
  jointRadius: number;
}

const DEFAULT_STYLE: DrawStyle = {
  bone: "#22d3ee",
  joint: "#e0f2fe",
  lineWidth: 2,
  jointRadius: 2.5,
};

export function drawSkeletonFrame(
  ctx: CanvasRenderingContext2D,
  frame: [number, number, number][],
  bounds: Bounds,
  width: number,
  height: number,
  style: DrawStyle = DEFAULT_STYLE,
): void {
  const scaleX = width / (bounds.maxX - bounds.minX);
  const scaleY = height / (bounds.maxY - bounds.minY);
  const scale = Math.min(scaleX, scaleY);
  const offsetX = (width - (bounds.maxX - bounds.minX) * scale) / 2;
  const offsetY = (height - (bounds.maxY - bounds.minY) * scale) / 2;
  const px = (x: number) => offsetX + (x - bounds.minX) * scale;
  const py = (y: number) => offsetY + (y - bounds.minY) * scale;

  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = style.bone;
  ctx.lineWidth = style.lineWidth;
  ctx.lineCap = "round";
  for (const [a, b] of HAND_CONNECTIONS) {
    const pa = frame[a];
    const pb = frame[b];
    if (pa === undefined || pb === undefined) continue;
    ctx.beginPath();
    ctx.moveTo(px(pa[0]), py(pa[1]));
    ctx.lineTo(px(pb[0]), py(pb[1]));
    ctx.stroke();
  }
  ctx.fillStyle = style.joint;
  for (const point of frame) {
    ctx.beginPath();
    ctx.arc(px(point[0]), py(point[1]), style.jointRadius, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** requestAnimationFrame loop rendering a looping skeleton animation. */
export class SkeletonLooper {
  private raf: number | null = null;
  private startedAt = 0;
  private bounds: Bounds;

  constructor(
    private canvas: HTMLCanvasElement,
    private animation: GestureAnimation,
    private style: DrawStyle = DEFAULT_STYLE,
  ) {
    this.bounds = fitBounds(animation.frames);
  }

  start(): void {
    if (this.raf !== null || this.animation.frames.length === 0) {
      return;
    }
    this.startedAt = performance.now();
    const tick = () => {
      const ctx = this.canvas.getContext("2d");
      if (ctx !== null) {
        const elapsed = (performance.now() - this.startedAt) / 1000;
        const index = frameIndexAt(this.animation.timestamps, elapsed);
        drawSkeletonFrame(
          ctx,
          this.animation.frames[index],
          this.bounds,
          this.canvas.width,
          this.canvas.height,
          this.style,
        );
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop(): void {
    if (this.raf !== null) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
  }
}
