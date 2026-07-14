import { describe, expect, it } from "vitest";
import { fitBounds, frameIndexAt, HAND_CONNECTIONS } from "./skeleton";

const frame = (offset: number): [number, number, number][] =>
  Array.from({ length: 21 }, (_, i) => [offset + i * 0.01, offset, 0]);

describe("fitBounds", () => {
  it("covers all frames with 10% padding", () => {
    const bounds = fitBounds([frame(0), frame(1)]);
    expect(bounds.minX).toBeLessThan(0);
    expect(bounds.maxX).toBeGreaterThan(1.2);
    expect(bounds.minY).toBeLessThan(0);
    expect(bounds.maxY).toBeGreaterThan(1);
  });

  it("returns a unit box for empty input", () => {
    expect(fitBounds([])).toEqual({ minX: 0, maxX: 1, minY: 0, maxY: 1 });
  });
});

describe("frameIndexAt", () => {
  const timestamps = [0, 0.1, 0.2, 0.3];

  it("selects the frame active at the elapsed time", () => {
    expect(frameIndexAt(timestamps, 0)).toBe(0);
    expect(frameIndexAt(timestamps, 0.15)).toBe(1);
    expect(frameIndexAt(timestamps, 0.25)).toBe(2);
  });

  it("loops past the end", () => {
    expect(frameIndexAt(timestamps, 0.35)).toBe(0);
    expect(frameIndexAt(timestamps, 0.75)).toBe(1);
  });

  it("handles degenerate timelines", () => {
    expect(frameIndexAt([], 1)).toBe(0);
    expect(frameIndexAt([5], 1)).toBe(0);
    expect(frameIndexAt([1, 1], 3)).toBe(0);
  });
});

describe("HAND_CONNECTIONS", () => {
  it("references only valid landmark indices", () => {
    for (const [a, b] of HAND_CONNECTIONS) {
      expect(a).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThan(21);
    }
  });
});
