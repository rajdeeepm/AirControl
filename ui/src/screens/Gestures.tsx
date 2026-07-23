import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
} from "react";

import {
  ACTION_GROUPS,
  actionKey,
  actionPayload,
  describeAction,
  mappingEnabled,
} from "../lib/actions";
import { BUILT_IN_GESTURES } from "../lib/constants";
import type { LibraryGesture, ServerEvent } from "../lib/types";
import {
  DaemonNotice,
  ErrorState,
  LoadingState,
  ScreenHeader,
  SkeletonPreview,
  ToggleSwitch,
} from "../lib/ui";
import type { AirControlClient, ConnectionState } from "../lib/ws";
import { GestureRecordingDialog, type SavedGesture } from "./GestureRecordingDialog";

type GesturesClient = Pick<
  AirControlClient,
  "on" | "onPreviewFrame" | "onState" | "request" | "send"
>;

export interface GesturesProps {
  client: GesturesClient;
  connectionState: ConnectionState;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred";
}

function requireLibrary(event: ServerEvent): LibraryGesture[] {
  if (event.type === "library") {
    return event.gestures;
  }
  if (event.type === "ack" && !event.ok) {
    throw new Error(event.error || "The gesture library is unavailable");
  }
  throw new Error(`Expected library, received ${event.type}`);
}

function requireAck(event: ServerEvent, operation: string): void {
  if (event.type === "ack" && event.ok) {
    return;
  }
  if (event.type === "ack") {
    throw new Error(event.error || `${operation} failed`);
  }
  throw new Error(`Expected acknowledgement, received ${event.type}`);
}

function actionForKey(key: string): Record<string, unknown> | null {
  for (const group of ACTION_GROUPS) {
    const option = group.options.find(
      (candidate) => actionKey(candidate.action) === key,
    );
    if (option !== undefined) {
      return option.action;
    }
  }
  return null;
}

function isKnownAction(key: string): boolean {
  return actionForKey(key) !== null;
}

function showDialog(dialog: HTMLDialogElement | null): void {
  if (dialog === null || dialog.open) {
    return;
  }
  if (typeof dialog.showModal === "function") {
    try {
      dialog.showModal();
      return;
    } catch {
      // Some embedded webviews expose showModal without implementing it.
    }
  }
  dialog.setAttribute("open", "");
}

function hideDialog(dialog: HTMLDialogElement | null): void {
  if (dialog === null) {
    return;
  }
  if (dialog.open && typeof dialog.close === "function") {
    try {
      dialog.close();
      return;
    } catch {
      // Fall through to the attribute fallback used by older webviews.
    }
  }
  dialog.removeAttribute("open");
}

function menuFocusTarget(button: HTMLButtonElement): HTMLElement {
  const details = button.closest("details");
  const summary = details?.querySelector<HTMLElement>("summary");
  details?.removeAttribute("open");
  return summary ?? button;
}

function navigateToCalibrationScreen(): void {
  // App owns navigation state through its existing sidebar controls. Activating
  // that control keeps this recording feature inside its strict screen scope.
  const navigation = document.querySelector<HTMLElement>(
    'nav[aria-label="Primary navigation"]',
  );
  const calibrationButton = Array.from(
    navigation?.querySelectorAll<HTMLButtonElement>("button") ?? [],
  ).find((button) => button.textContent?.trim() === "Calibration");
  calibrationButton?.focus();
  calibrationButton?.click();
}

export function Gestures({ client, connectionState }: GesturesProps) {
  const [gestures, setGestures] = useState<LibraryGesture[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [busyGestureId, setBusyGestureId] = useState<number | null>(null);

  const [renameTarget, setRenameTarget] = useState<LibraryGesture | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<LibraryGesture | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [recordingOpen, setRecordingOpen] = useState(false);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [mapTargetId, setMapTargetId] = useState<number | null>(null);

  const requestGeneration = useRef(0);
  const renameDialogRef = useRef<HTMLDialogElement>(null);
  const deleteDialogRef = useRef<HTMLDialogElement>(null);
  const dialogReturnFocusRef = useRef<HTMLElement | null>(null);
  const addGestureButtonRef = useRef<HTMLButtonElement>(null);
  const gestureCardRefs = useRef<Map<number, HTMLElement>>(new Map());
  const gestureSelectRefs = useRef<Map<number, HTMLSelectElement>>(new Map());

  const loadLibrary = useCallback(async (): Promise<LibraryGesture[] | null> => {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    setLoading(true);
    setLoadError(null);
    try {
      const event = await client.request("list_library");
      const nextGestures = requireLibrary(event);
      if (requestGeneration.current === generation) {
        setGestures(nextGestures);
      }
      return nextGestures;
    } catch (error) {
      if (requestGeneration.current === generation) {
        setLoadError(errorMessage(error));
      }
      return null;
    } finally {
      if (requestGeneration.current === generation) {
        setLoading(false);
      }
    }
  }, [client]);

  useEffect(() => {
    if (connectionState === "open") {
      void loadLibrary();
    } else {
      requestGeneration.current += 1;
      setGestures(null);
      setLoading(false);
      setLoadError(null);
      setUpdateError(null);
      setRenameTarget(null);
      setDeleteTarget(null);
      setSaveNotice(null);
      setMapTargetId(null);
    }
    return () => {
      requestGeneration.current += 1;
    };
  }, [connectionState, loadLibrary]);

  useEffect(() => {
    if (renameTarget !== null) {
      showDialog(renameDialogRef.current);
    }
  }, [renameTarget]);

  useEffect(() => {
    if (deleteTarget !== null) {
      showDialog(deleteDialogRef.current);
    }
  }, [deleteTarget]);

  // Once the just-recorded gesture is mapped (or disappears, e.g. deleted),
  // the "map this gesture" guidance has done its job and clears itself.
  useEffect(() => {
    if (mapTargetId === null || gestures === null) {
      return;
    }
    const target = gestures.find((candidate) => candidate.id === mapTargetId);
    if (target === undefined || target.mapping !== null) {
      setMapTargetId(null);
    }
  }, [gestures, mapTargetId]);

  // Guide the user straight into mapping the newly recorded gesture: bring
  // its card into view and hand focus to its action select.
  useEffect(() => {
    if (mapTargetId === null) {
      return;
    }
    const card = gestureCardRefs.current.get(mapTargetId);
    if (card !== undefined && typeof card.scrollIntoView === "function") {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    gestureSelectRefs.current.get(mapTargetId)?.focus();
  }, [mapTargetId]);

  const dismissMapPrompt = useCallback(() => {
    setMapTargetId(null);
  }, []);

  const restoreDialogFocus = () => {
    const returnTarget = dialogReturnFocusRef.current;
    dialogReturnFocusRef.current = null;
    returnTarget?.focus();
  };

  const closeRenameDialog = () => {
    hideDialog(renameDialogRef.current);
    setRenameTarget(null);
    setRenameDraft("");
    setRenameError(null);
    restoreDialogFocus();
  };

  const closeDeleteDialog = () => {
    hideDialog(deleteDialogRef.current);
    setDeleteTarget(null);
    setDeleteError(null);
    restoreDialogFocus();
  };

  const finishRecording = useCallback(
    (gesture: SavedGesture) => {
      setRecordingOpen(false);
      setSaveNotice(null);
      setMapTargetId(null);
      void loadLibrary().then((nextGestures) => {
        if (nextGestures === null) {
          // The reload failed; fall back to a passive confirmation since we
          // have no library to resolve or highlight the new gesture within.
          setSaveNotice(gesture.name);
          return;
        }
        const resolved =
          (gesture.id !== null
            ? nextGestures.find((candidate) => candidate.id === gesture.id)
            : undefined) ??
          nextGestures.find((candidate) => candidate.name === gesture.name);
        if (resolved === undefined) {
          setSaveNotice(gesture.name);
          return;
        }
        setMapTargetId(resolved.id);
      });
    },
    [loadLibrary],
  );

  const beginRename = (
    gesture: LibraryGesture,
    event: MouseEvent<HTMLButtonElement>,
  ) => {
    dialogReturnFocusRef.current = menuFocusTarget(event.currentTarget);
    setRenameDraft(gesture.name);
    setRenameError(null);
    setRenameTarget(gesture);
  };

  const beginDelete = (
    gesture: LibraryGesture,
    event: MouseEvent<HTMLButtonElement>,
  ) => {
    dialogReturnFocusRef.current = menuFocusTarget(event.currentTarget);
    setDeleteError(null);
    setDeleteTarget(gesture);
  };

  const updateMapping = async (
    gesture: LibraryGesture,
    action: Record<string, unknown>,
    enabled = gesture.mapping === null ? true : mappingEnabled(gesture.mapping),
  ) => {
    setBusyGestureId(gesture.id);
    setUpdateError(null);
    try {
      const event = await client.request("set_mapping", {
        gesture_id: gesture.id,
        action: actionPayload(action),
        enabled,
      });
      requireAck(event, "Mapping update");
      await loadLibrary();
    } catch (error) {
      setUpdateError(errorMessage(error));
    } finally {
      setBusyGestureId(null);
    }
  };

  const submitRename = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (renameTarget === null) {
      return;
    }
    const newName = renameDraft.trim();
    if (newName.length === 0) {
      setRenameError("Gesture name cannot be empty.");
      return;
    }

    setBusyGestureId(renameTarget.id);
    setRenameError(null);
    try {
      const reply = await client.request("rename_gesture", {
        gesture_id: renameTarget.id,
        new_name: newName,
      });
      requireAck(reply, "Rename");
      closeRenameDialog();
      await loadLibrary();
    } catch (error) {
      setRenameError(errorMessage(error));
    } finally {
      setBusyGestureId(null);
    }
  };

  const confirmDelete = async () => {
    if (deleteTarget === null) {
      return;
    }

    setBusyGestureId(deleteTarget.id);
    setDeleteError(null);
    try {
      const reply = await client.request("delete_gesture", {
        gesture_id: deleteTarget.id,
      });
      requireAck(reply, "Delete");
      closeDeleteDialog();
      await loadLibrary();
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setBusyGestureId(null);
    }
  };

  const disconnected = connectionState !== "open";

  return (
    <div className="screen gestures-screen">
      <ScreenHeader
        title="Gestures"
        description="Review your custom gestures and choose what each deliberate movement does."
      />

      {disconnected ? (
        <DaemonNotice />
      ) : (
        <section className="screen-section" aria-labelledby="custom-gestures-title">
          <div className="section-heading">
            <div>
              <h2 id="custom-gestures-title">Custom gestures</h2>
              <p>Keep the set focused so every gesture remains easy to remember.</p>
            </div>
            {loading && gestures !== null ? (
              <span className="inline-status" role="status" aria-live="polite">
                Refreshing…
              </span>
            ) : null}
          </div>

          {loading && gestures === null ? (
            <LoadingState>Loading gestures…</LoadingState>
          ) : null}

          {loadError !== null ? (
            <ErrorState
              title="Could not load gestures"
              detail={loadError}
              onRetry={() => void loadLibrary()}
            />
          ) : null}

          {updateError !== null ? (
            <ErrorState title="Could not update the gesture" detail={updateError} />
          ) : null}

          {!loading && loadError === null && gestures?.length === 0 ? (
            <div className="state-panel gesture-empty">
              <strong>No custom gestures yet</strong>
              <span>
                Select <strong>+ Add Gesture</strong> to record your first motion.
              </span>
            </div>
          ) : null}

          {gestures !== null && gestures.length > 0 ? (
            <div className="gesture-grid" aria-label="Custom gesture mappings">
              {gestures.map((gesture) => {
                const currentActionKey =
                  gesture.mapping === null ? "" : actionKey(gesture.mapping);
                const unknownCurrentAction =
                  currentActionKey !== "" && !isKnownAction(currentActionKey);
                const busy = busyGestureId === gesture.id;
                const enabled = mappingEnabled(gesture.mapping);
                const needsMapping =
                  gesture.id === mapTargetId && gesture.mapping === null;
                const mapPromptId = `gesture-map-prompt-${gesture.id}`;

                return (
                  <article
                    className={
                      needsMapping
                        ? "gesture-card gesture-card--needs-mapping"
                        : "gesture-card"
                    }
                    key={gesture.id}
                    aria-busy={busy}
                    data-needs-mapping={needsMapping ? "true" : undefined}
                    ref={(element) => {
                      if (element === null) {
                        gestureCardRefs.current.delete(gesture.id);
                      } else {
                        gestureCardRefs.current.set(gesture.id, element);
                      }
                    }}
                  >
                    <SkeletonPreview
                      animation={gesture.animation}
                      label={`Skeleton animation for ${gesture.name}`}
                    />

                    <div className="gesture-card-heading">
                      <div>
                        <h3>
                          {gesture.name}
                          {gesture.kind === "pose" ? (
                            <span className="gesture-kind-badge">Pose</span>
                          ) : null}
                        </h3>
                        <p>
                          {gesture.description.trim() === ""
                            ? "No description provided."
                            : gesture.description}
                        </p>
                      </div>
                      <details className="gesture-menu">
                        <summary aria-label={`More options for ${gesture.name}`}>
                          <span aria-hidden="true">...</span>
                        </summary>
                        <div className="gesture-menu-options">
                          <button
                            type="button"
                            disabled={busyGestureId !== null}
                            onClick={(event) => beginRename(gesture, event)}
                          >
                            Rename
                          </button>
                          <button
                            className="danger-text"
                            type="button"
                            disabled={busyGestureId !== null}
                            onClick={(event) => beginDelete(gesture, event)}
                          >
                            Delete
                          </button>
                        </div>
                      </details>
                    </div>

                    <div className="gesture-action-summary">
                      <span>Current action</span>
                      <strong>{describeAction(gesture.mapping)}</strong>
                    </div>

                    {needsMapping ? (
                      <div
                        className="gesture-map-prompt"
                        id={mapPromptId}
                        role="status"
                        aria-live="polite"
                      >
                        <span>
                          Now choose what <strong>“{gesture.name}”</strong> does
                          — it won’t do anything until you map it.
                        </span>
                        <button
                          type="button"
                          className="gesture-map-prompt-dismiss"
                          onClick={dismissMapPrompt}
                        >
                          Dismiss
                        </button>
                      </div>
                    ) : null}

                    <label
                      className="field-label"
                      htmlFor={`gesture-action-${gesture.id}`}
                    >
                      Change mapped action
                    </label>
                    <select
                      id={`gesture-action-${gesture.id}`}
                      value={currentActionKey}
                      disabled={busyGestureId !== null}
                      aria-label={`Mapped action for ${gesture.name}`}
                      aria-describedby={needsMapping ? mapPromptId : undefined}
                      ref={(element) => {
                        if (element === null) {
                          gestureSelectRefs.current.delete(gesture.id);
                        } else {
                          gestureSelectRefs.current.set(gesture.id, element);
                        }
                      }}
                      onChange={(event) => {
                        const action = actionForKey(event.currentTarget.value);
                        if (action !== null) {
                          void updateMapping(gesture, action);
                        }
                      }}
                    >
                      <option value="" disabled>
                        Not assigned — choose an action
                      </option>
                      {unknownCurrentAction ? (
                        <option value={currentActionKey}>
                          Current: {describeAction(gesture.mapping)}
                        </option>
                      ) : null}
                      {ACTION_GROUPS.map((group) => (
                        <optgroup key={group.label} label={group.label}>
                          {group.options.map((option) => (
                            <option
                              key={actionKey(option.action)}
                              value={actionKey(option.action)}
                            >
                              {option.label}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </select>

                    <div className="gesture-enable-control">
                      <div>
                        <span>Mapped action</span>
                        <strong>
                          {gesture.mapping === null
                            ? "No action assigned"
                            : enabled
                              ? "Enabled"
                              : "Disabled"}
                        </strong>
                      </div>
                      <ToggleSwitch
                        checked={enabled}
                        disabled={
                          gesture.mapping === null || busyGestureId !== null
                        }
                        label={`${enabled ? "Disable" : "Enable"} ${gesture.name}`}
                        onChange={(nextEnabled) => {
                          if (gesture.mapping !== null) {
                            void updateMapping(
                              gesture,
                              gesture.mapping,
                              nextEnabled,
                            );
                          }
                        }}
                      />
                    </div>
                    {busy ? (
                      <span className="inline-status" role="status">
                        Saving…
                      </span>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ) : null}
        </section>
      )}

      {saveNotice === null ? null : (
        <p className="success-message" role="status" aria-live="polite">
          Gesture saved: “{saveNotice}”. Find it above to choose what it does.
        </p>
      )}

      <section className="add-gesture" aria-labelledby="add-gesture-title">
        <div className="add-gesture-mark" aria-hidden="true">
          +
        </div>
        <div className="add-gesture-copy">
          <h2 id="add-gesture-title">Add Gesture</h2>
          <p>
            Record a focused set of deliberate examples, review each take, and
            save the gesture without leaving the app.
          </p>
        </div>
        <button
          ref={addGestureButtonRef}
          className="button button-primary add-gesture-action"
          type="button"
          aria-haspopup="dialog"
          aria-controls="record-gesture-dialog"
          onClick={() => {
            setSaveNotice(null);
            setMapTargetId(null);
            setRecordingOpen(true);
          }}
        >
          + Add Gesture
        </button>
      </section>

      <section
        className="built-in-reference"
        aria-labelledby="built-in-reference-title"
      >
        <div className="section-heading">
          <div>
            <h2 id="built-in-reference-title">Built-in gesture reference</h2>
            <p>
              These fixed safety and pointer controls are always available and
              cannot be renamed, deleted, or remapped.
            </p>
          </div>
        </div>
        <div
          className="table-scroll"
          role="region"
          aria-label="Built-in gesture vocabulary"
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                <th scope="col">Gesture</th>
                <th scope="col">Action</th>
                <th scope="col">How to use it</th>
              </tr>
            </thead>
            <tbody>
              {BUILT_IN_GESTURES.map((gesture) => (
                <tr key={gesture.gesture}>
                  <th scope="row">{gesture.gesture}</th>
                  <td>{gesture.action}</td>
                  <td>{gesture.guidance}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {renameTarget === null ? null : (
        <dialog
          ref={renameDialogRef}
          className="confirm-dialog"
          aria-labelledby="rename-gesture-title"
          aria-describedby="rename-gesture-description"
          onCancel={(event) => {
            event.preventDefault();
            if (busyGestureId === null) {
              closeRenameDialog();
            }
          }}
          onClose={() => {
            setRenameTarget(null);
            setRenameDraft("");
            setRenameError(null);
            restoreDialogFocus();
          }}
        >
          <form onSubmit={(event) => void submitRename(event)}>
            <h2 id="rename-gesture-title">Rename gesture</h2>
            <p id="rename-gesture-description">
              Choose a short, memorable name for “{renameTarget.name}”.
            </p>
            <label className="field-label" htmlFor="rename-gesture-input">
              Gesture name
            </label>
            <input
              id="rename-gesture-input"
              autoFocus
              value={renameDraft}
              disabled={busyGestureId !== null}
              onChange={(event) => setRenameDraft(event.target.value)}
            />
            {renameError === null ? null : <p role="alert">{renameError}</p>}
            <div className="dialog-actions">
              <button
                className="button button-secondary"
                type="button"
                disabled={busyGestureId !== null}
                onClick={closeRenameDialog}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                type="submit"
                disabled={
                  busyGestureId !== null || renameDraft.trim().length === 0
                }
              >
                {busyGestureId === renameTarget.id ? "Saving…" : "Save name"}
              </button>
            </div>
          </form>
        </dialog>
      )}

      {deleteTarget === null ? null : (
        <dialog
          ref={deleteDialogRef}
          className="confirm-dialog"
          aria-labelledby="delete-gesture-title"
          aria-describedby="delete-gesture-description"
          onCancel={(event) => {
            event.preventDefault();
            if (busyGestureId === null) {
              closeDeleteDialog();
            }
          }}
          onClose={() => {
            setDeleteTarget(null);
            setDeleteError(null);
            restoreDialogFocus();
          }}
        >
          <h2 id="delete-gesture-title">Delete custom gesture?</h2>
          <p id="delete-gesture-description">
            Delete “{deleteTarget.name}” and all of its stored skeleton
            exemplars? This cannot be undone.
          </p>
          {deleteError === null ? null : <p role="alert">{deleteError}</p>}
          <div className="dialog-actions">
            <button
              className="button button-secondary"
              type="button"
              disabled={busyGestureId !== null}
              onClick={closeDeleteDialog}
            >
              Cancel
            </button>
            <button
              className="button button-danger"
              type="button"
              disabled={busyGestureId !== null}
              onClick={() => void confirmDelete()}
            >
              {busyGestureId === deleteTarget.id
                ? "Deleting…"
                : "Delete gesture"}
            </button>
          </div>
        </dialog>
      )}

      {recordingOpen ? (
        <GestureRecordingDialog
          client={client}
          connectionState={connectionState}
          returnFocusRef={addGestureButtonRef}
          onDismiss={() => setRecordingOpen(false)}
          onSaved={finishRecording}
          onNavigateCalibration={navigateToCalibrationScreen}
        />
      ) : null}
    </div>
  );
}

export default Gestures;
