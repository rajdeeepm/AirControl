import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  drawSkeletonFrame,
  fitBounds,
  SkeletonLooper,
} from "../lib/skeleton";
import type { GestureAnimation, LibraryGesture, ServerEvent } from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type ScreenClient = Pick<
  AirControlClient,
  "on" | "onState" | "send" | "request"
>;

export interface LibraryProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

const BUILT_IN_GESTURES = [
  ["Open palm hold", "Arm"],
  ["Fist hold", "Pause"],
  ["Index point", "Move pointer"],
  ["Thumb+index pinch", "Click/drag"],
  ["Two fingers", "Scroll"],
  ["Three-finger swipe left/right", "Next/previous app"],
  ["Three-finger swipe up", "Task view"],
  ["Three-finger swipe down", "Show desktop"],
] as const;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

function requireSuccessfulAck(event: ServerEvent, operation: string): void {
  if (event.type !== "ack") {
    throw new Error(`Expected acknowledgement, received ${event.type}`);
  }
  if (!event.ok) {
    throw new Error(event.error || `${operation} failed`);
  }
}

function mappingLabel(mapping: Record<string, unknown> | null): string {
  if (mapping === null) {
    return "Not assigned";
  }

  const kind = mapping.kind;
  if (typeof kind !== "string") {
    return "Mapped action";
  }

  if (kind === "hotkey") {
    const keys = mapping.keys;
    if (Array.isArray(keys) && keys.every((key) => typeof key === "number")) {
      return `Hotkey (${keys.join(" + ")})`;
    }
    return "Hotkey";
  }

  if (kind === "scroll" && typeof mapping.amount === "number") {
    if (mapping.amount > 0) return "Scroll up";
    if (mapping.amount < 0) return "Scroll down";
    return "Scroll (no movement)";
  }

  const labels: Record<string, string> = {
    show_desktop: "Show desktop",
    switch_next: "Next app",
    switch_previous: "Previous app",
    task_view: "Task view",
  };
  return labels[kind] ?? kind.replaceAll("_", " ");
}

function drawStillFrame(
  canvas: HTMLCanvasElement,
  animation: GestureAnimation,
): void {
  const frame = animation.frames[0];
  if (frame === undefined) {
    return;
  }
  const context = canvas.getContext("2d");
  if (context === null) {
    return;
  }
  drawSkeletonFrame(
    context,
    frame,
    fitBounds(animation.frames),
    canvas.width,
    canvas.height,
  );
}

function GesturePreview({ gesture }: { gesture: LibraryGesture }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animation = gesture.animation;

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

    const renderForPreference = (reduceMotion: boolean) => {
      looper?.stop();
      looper = null;
      if (reduceMotion || typeof requestAnimationFrame !== "function") {
        drawStillFrame(canvas, animation);
        return;
      }
      looper = new SkeletonLooper(canvas, animation);
      looper.start();
    };

    renderForPreference(motionQuery?.matches ?? false);

    const handleMotionPreference = (event: MediaQueryListEvent) => {
      renderForPreference(event.matches);
    };
    motionQuery?.addEventListener("change", handleMotionPreference);

    return () => {
      motionQuery?.removeEventListener("change", handleMotionPreference);
      looper?.stop();
    };
  }, [animation]);

  return (
    <div className="gesture-preview">
      <canvas
        ref={canvasRef}
        width={560}
        height={280}
        role="img"
        aria-label={
          animation === null || animation.frames.length === 0
            ? `No skeleton preview stored for ${gesture.name}`
            : `Skeleton preview for ${gesture.name}`
        }
      />
      {(animation === null || animation.frames.length === 0) && (
        <span>No skeleton preview stored.</span>
      )}
    </div>
  );
}

function BuiltInVocabulary() {
  return (
    <section className="builtins" aria-labelledby="builtins-title">
      <h2 id="builtins-title">Built-in gestures</h2>
      <p>
        Built-in gestures are fixed system controls and cannot be renamed,
        deleted, or remapped.
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Gesture</th>
            <th scope="col">Action</th>
          </tr>
        </thead>
        <tbody>
          {BUILT_IN_GESTURES.map(([gesture, action]) => (
            <tr key={gesture}>
              <td>{gesture}</td>
              <td>{action}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function Library({ client, connectionState }: LibraryProps) {
  const [gestures, setGestures] = useState<LibraryGesture[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draftName, setDraftName] = useState("");
  const [busyGestureId, setBusyGestureId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<LibraryGesture | null>(
    null,
  );
  const deleteDialogRef = useRef<HTMLDialogElement>(null);
  const requestGeneration = useRef(0);

  const refreshLibrary = useCallback(async () => {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    setLoading(true);
    setError(null);
    try {
      const event = await client.request("list_library");
      if (event.type !== "library") {
        throw new Error(`Expected library, received ${event.type}`);
      }
      if (requestGeneration.current === generation) {
        setGestures(event.gestures);
        setLoaded(true);
      }
    } catch (requestError) {
      if (requestGeneration.current === generation) {
        setError(errorMessage(requestError));
        setLoaded(true);
      }
    } finally {
      if (requestGeneration.current === generation) {
        setLoading(false);
      }
    }
  }, [client]);

  useEffect(() => {
    if (connectionState === "open") {
      void refreshLibrary();
    }
  }, [connectionState, refreshLibrary]);

  useEffect(() => {
    const dialog = deleteDialogRef.current;
    if (pendingDelete === null || dialog === null || dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }, [pendingDelete]);

  const closeDeleteDialog = () => {
    const dialog = deleteDialogRef.current;
    if (dialog !== null && dialog.open && typeof dialog.close === "function") {
      dialog.close();
      return;
    }
    dialog?.removeAttribute("open");
    setPendingDelete(null);
  };

  const beginRename = (gesture: LibraryGesture) => {
    setError(null);
    setEditingId(gesture.id);
    setDraftName(gesture.name);
  };

  const renameGesture = async (
    event: FormEvent<HTMLFormElement>,
    gesture: LibraryGesture,
  ) => {
    event.preventDefault();
    const newName = draftName.trim();
    if (newName.length === 0) {
      setError("Gesture name cannot be empty.");
      return;
    }

    setBusyGestureId(gesture.id);
    setError(null);
    try {
      const reply = await client.request("rename_gesture", {
        gesture_id: gesture.id,
        new_name: newName,
      });
      requireSuccessfulAck(reply, "Rename");
      setEditingId(null);
      setDraftName("");
      await refreshLibrary();
    } catch (renameError) {
      setError(errorMessage(renameError));
    } finally {
      setBusyGestureId(null);
    }
  };

  const deleteGesture = async () => {
    if (pendingDelete === null) {
      return;
    }

    const gesture = pendingDelete;
    setBusyGestureId(gesture.id);
    setError(null);
    try {
      const reply = await client.request("delete_gesture", {
        gesture_id: gesture.id,
      });
      requireSuccessfulAck(reply, "Delete");
      closeDeleteDialog();
      await refreshLibrary();
    } catch (deleteError) {
      setError(errorMessage(deleteError));
      closeDeleteDialog();
    } finally {
      setBusyGestureId(null);
    }
  };

  const disconnected = connectionState !== "open";
  const initialLoading =
    !disconnected && error === null && gestures.length === 0 && !loaded;

  return (
    <>
      <header>
        <h1>Library</h1>
        <p>Review custom gestures, their evidence, and assigned actions.</p>
      </header>

      {disconnected ? (
        <div className="empty" role="status">
          <strong>
            Daemon not connected — start AirControl with app.cmd
          </strong>
        </div>
      ) : (
        <>
          {error !== null ? (
            <div className="empty" role="alert">
              <strong>Could not load the gesture library.</strong>
              <p>{error}</p>
              <button className="btn" type="button" onClick={() => void refreshLibrary()}>
                Try again
              </button>
            </div>
          ) : null}

          {initialLoading || (loading && gestures.length === 0) ? (
            <div className="panel" role="status" aria-live="polite">
              Loading gesture library…
            </div>
          ) : null}

          {loaded && !loading && error === null && gestures.length === 0 ? (
            <div className="empty">
              <strong>No custom gestures recorded yet.</strong>
              <p>
                Run <span className="mono">record.cmd</span> to teach AirControl
                your first deliberate gesture.
              </p>
            </div>
          ) : null}

          {loading && gestures.length > 0 ? (
            <p role="status" aria-live="polite">
              Refreshing library…
            </p>
          ) : null}

          {gestures.length > 0 ? (
            <div className="gesture-grid" aria-label="Custom gestures">
              {gestures.map((gesture) => {
                const isBusy = busyGestureId === gesture.id;
                const isEditing = editingId === gesture.id;
                return (
                  <article className="gesture-card" key={gesture.id}>
                    <GesturePreview gesture={gesture} />

                    {isEditing ? (
                      <form
                        className="row"
                        onSubmit={(event) => void renameGesture(event, gesture)}
                      >
                        <input
                          autoFocus
                          aria-label={`New name for ${gesture.name}`}
                          value={draftName}
                          onChange={(event) => setDraftName(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") {
                              setEditingId(null);
                              setDraftName("");
                            }
                          }}
                        />
                        <button
                          className="btn primary"
                          type="submit"
                          disabled={isBusy || draftName.trim().length === 0}
                        >
                          {isBusy ? "Saving…" : "Save"}
                        </button>
                        <button
                          className="btn"
                          type="button"
                          disabled={isBusy}
                          onClick={() => {
                            setEditingId(null);
                            setDraftName("");
                          }}
                        >
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <div className="row">
                        <h3>
                          <button
                            className="btn"
                            type="button"
                            aria-label={`Rename ${gesture.name}`}
                            onClick={() => beginRename(gesture)}
                          >
                            {gesture.name}
                          </button>
                        </h3>
                        <button
                          className="btn danger"
                          type="button"
                          disabled={isBusy}
                          onClick={() => {
                            setError(null);
                            setPendingDelete(gesture);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    )}

                    <div className="stats">
                      {gesture.exemplar_count} exemplars · {gesture.confirms}
                      {" confirms / "}
                      {gesture.rejects} rejects · threshold offset{" "}
                      <span className="mono">
                        {gesture.threshold_offset.toFixed(2)}
                      </span>
                    </div>
                    <div className="stats">
                      Current mapping: {mappingLabel(gesture.mapping)}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : null}
        </>
      )}

      <BuiltInVocabulary />

      <dialog
        ref={deleteDialogRef}
        className="confirm"
        aria-labelledby="delete-gesture-title"
        onClose={() => setPendingDelete(null)}
      >
        <h2 id="delete-gesture-title">Delete custom gesture?</h2>
        <p>
          {pendingDelete === null
            ? "This permanently removes the gesture and its stored exemplars."
            : `Delete “${pendingDelete.name}” and all of its stored exemplars?`}
        </p>
        <div className="row">
          <button
            className="btn"
            type="button"
            disabled={busyGestureId !== null}
            onClick={closeDeleteDialog}
          >
            Cancel
          </button>
          <button
            className="btn danger"
            type="button"
            disabled={pendingDelete === null || busyGestureId !== null}
            onClick={() => void deleteGesture()}
          >
            {busyGestureId === pendingDelete?.id ? "Deleting…" : "Delete"}
          </button>
        </div>
      </dialog>
    </>
  );
}
