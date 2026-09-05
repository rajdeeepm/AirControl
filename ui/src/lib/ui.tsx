import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  drawSkeletonFrame,
  fitBounds,
  SkeletonLooper,
  type DrawStyle,
} from "./skeleton";
import type { GestureAnimation } from "./types";

export function ScreenHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="screen-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions === undefined ? null : (
        <div className="screen-header-actions">{actions}</div>
      )}
    </header>
  );
}

export function DaemonNotice() {
  return (
    <div className="state-panel state-panel-offline" role="status">
      <strong>Daemon not connected</strong>
      <span>
        If you started the app, its gesture engine stopped — check the
        terminal for the reason. Otherwise run app.cmd (Windows) or
        ./app.sh (macOS) and leave this window open.
      </span>
    </div>
  );
}

export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <div className="state-panel" role="status" aria-live="polite">
      <span className="loading-mark" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export function ErrorState({
  title,
  detail,
  onRetry,
}: {
  title: string;
  detail: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-panel state-panel-error" role="alert">
      <strong>{title}</strong>
      <span>{detail}</span>
      {onRetry === undefined ? null : (
        <button className="button button-secondary" type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function ToggleSwitch({
  checked,
  onChange,
  label,
  labelledBy,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  labelledBy?: string;
  disabled?: boolean;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    if (!event.repeat) {
      onChange(!checked);
    }
  };

  return (
    <button
      className="switch"
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={labelledBy === undefined ? label : undefined}
      aria-labelledby={labelledBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      onKeyDown={handleKeyDown}
    >
      <span className="switch-thumb" aria-hidden="true" />
    </button>
  );
}

export interface SettingRangeProps {
  id: string;
  label: string;
  helper: string;
  value: number;
  min: number;
  max: number;
  step: number;
  formatValue?: (value: number) => string;
  onCommit: (value: number) => void | Promise<void>;
  disabled?: boolean;
}

export function SettingRange({
  id,
  label,
  helper,
  value,
  min,
  max,
  step,
  formatValue = String,
  onCommit,
  disabled = false,
}: SettingRangeProps) {
  const [draft, setDraft] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRef = useRef(value);
  const onCommitRef = useRef(onCommit);

  useEffect(() => {
    onCommitRef.current = onCommit;
  }, [onCommit]);

  useEffect(() => {
    if (timerRef.current === null) {
      setDraft(value);
      draftRef.current = value;
    }
  }, [value]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        void onCommitRef.current(draftRef.current);
      }
    },
    [],
  );

  const flushPendingCommit = () => {
    if (timerRef.current === null) {
      return;
    }
    clearTimeout(timerRef.current);
    timerRef.current = null;
    void onCommitRef.current(draftRef.current);
  };

  const updateDraft = (next: number) => {
    setDraft(next);
    draftRef.current = next;
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
    }
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void onCommitRef.current(next);
    }, 150);
  };

  return (
    <div className="setting-range">
      <div className="setting-range-heading">
        <label htmlFor={id}>{label}</label>
        <output className="number-readout" htmlFor={id}>
          {formatValue(draft)}
        </output>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={draft}
        disabled={disabled}
        aria-describedby={`${id}-help`}
        aria-valuetext={formatValue(draft)}
        onChange={(event) => updateDraft(Number(event.target.value))}
        onBlur={flushPendingCommit}
        onPointerUp={flushPendingCommit}
      />
      <p id={`${id}-help`}>{helper}</p>
    </div>
  );
}

function skeletonStyle(canvas: HTMLCanvasElement): DrawStyle {
  const styles = getComputedStyle(canvas);
  return {
    bone: styles.getPropertyValue("--skeleton-bone").trim() || "#22d3ee",
    joint: styles.getPropertyValue("--skeleton-joint").trim() || "#e0f2fe",
    lineWidth: 2.5,
    jointRadius: 3,
  };
}

function drawFirstFrame(
  canvas: HTMLCanvasElement,
  animation: GestureAnimation,
): void {
  const frame = animation.frames[0];
  const context = canvas.getContext("2d");
  if (frame === undefined || context === null) {
    return;
  }
  drawSkeletonFrame(
    context,
    frame,
    fitBounds(animation.frames),
    canvas.width,
    canvas.height,
    skeletonStyle(canvas),
  );
}

export function SkeletonPreview({
  animation,
  label,
}: {
  animation: GestureAnimation | null;
  label: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null || animation === null || animation.frames.length === 0) {
      return;
    }

    const motionQuery =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-reduced-motion: reduce)")
        : null;
    let looper: SkeletonLooper | null = null;

    const render = () => {
      looper?.stop();
      looper = null;
      if (
        motionQuery?.matches === true ||
        typeof requestAnimationFrame !== "function"
      ) {
        drawFirstFrame(canvas, animation);
        return;
      }
      looper = new SkeletonLooper(canvas, animation, skeletonStyle(canvas));
      looper.start();
    };

    const handleMotion = () => render();
    const themeObserver =
      typeof MutationObserver === "function"
        ? new MutationObserver(render)
        : null;

    render();
    motionQuery?.addEventListener("change", handleMotion);
    themeObserver?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    return () => {
      motionQuery?.removeEventListener("change", handleMotion);
      themeObserver?.disconnect();
      looper?.stop();
    };
  }, [animation]);

  const hasAnimation = animation !== null && animation.frames.length > 0;
  return (
    <div className="skeleton-preview" data-empty={!hasAnimation}>
      <canvas
        ref={canvasRef}
        width={520}
        height={220}
        role="img"
        aria-label={hasAnimation ? label : `${label}. No preview stored.`}
      />
      {hasAnimation ? null : <span>No skeleton preview stored</span>}
    </div>
  );
}
