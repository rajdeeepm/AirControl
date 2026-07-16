# AirControl — Spec 9: Two-Hand Click Mode

**Date:** 2026-07-16
**Status:** Approved (user chose two-hand mode over single-hand pinch tuning and depth)

## 0. Why
Single-hand pinch shares one hand for pointing and clicking, so the pinch motion
drifts the cursor; the freeze/dead-zone mitigation feels sticky/delayed.
**Two-hand mode decouples them:** the dominant hand points (moves the cursor);
the other hand pinches to click. The clicking motion cannot move the cursor
because a different hand controls position — no freeze, no dead-zone, no lag.

Single-hand mode stays the default and unchanged; two-hand is an opt-in
`click_mode` so nothing regresses for one-handed use.

## 1. Model
- **Pointer hand = the dominant hand** (from the existing `dominant_hand`
  setting). It drives everything it does today: arming (wake pose), pointer
  movement, scroll, three-finger swipe. In two-hand mode its OWN pinch does
  **not** click (clicking is delegated to the other hand).
- **Click hand = the non-dominant hand.** A pinch (thumb-to-finger) →
  `LEFT_DOWN` at the pointer's current position; release → `LEFT_UP`. Drag =
  hold the pinch on the click hand while the pointer hand moves. Clicking never
  emits pointer movement.
- If only one hand is visible in two-hand mode, it acts as the pointer hand
  (can still move/scroll/swipe/arm); clicking simply isn't available until the
  second hand appears. No errors, no stuck state.

## 2. Changes
1. **`click_mode` app-setting**: `"single"` (default) | `"two_hand"`, persisted +
   live via set_app_setting. In `two_hand`, the daemon tracks up to 2 hands.
2. **`TrackingConfig.max_hands`**: relax validation to allow 1 **or** 2 (default
   1). The daemon sets it to 2 when click_mode is two_hand (a recalibrate/restart
   of the tracker is acceptable when the mode changes; do it cleanly).
3. **Tracker returns multiple hands**: add `HandTracker.detect_hands(rgb, ts) ->
   tuple[HandObservation, ...]` (up to num_hands, each with handedness). Keep the
   single-best `detect()` behavior available so single mode is byte-for-byte
   unchanged.
4. **Frame snapshot / feed carries hands**: the vision worker's snapshot and
   `daemon.feed(...)` carry up to two observations (a tuple). Single mode uses
   the first/best exactly as today.
5. **Two-hand coordination in the pipeline** (additive; the existing single-hand
   `GestureEngine` is untouched and still runs the pointer hand):
   - Assign roles by handedness vs `dominant_hand` (respect mirroring — reuse the
     recognizer's existing handedness/mirror handling).
   - Run the engine on the pointer hand for move/scroll/swipe/arm.
   - Evaluate the click hand's pinch independently and inject `LEFT_DOWN`/
     `LEFT_UP` (via the same releasing-action-safe path) at the current cursor —
     no pointer movement from the click hand.
   - The pinch-approach freeze / drag dead-zone are single-mode only; two-hand
     mode does not use them (that removes the stickiness).
6. **Overlay / preview**: draw both hands' skeletons when two are tracked
   (nice-to-have; do not block on it).
7. **Airy**: the two-handed reference cards already exist — a two-hand click can
   drive the existing drag/two-hand visual (optional follow-up).

## 3. Non-goals
- Depth / push-to-click (deferred to v2 per the build direction; single-webcam
  depth is too noisy for the false-positive budget).
- Ten-finger / bimanual gestures beyond point+click (v2 headline).

## 4. Definition of Done
- `click_mode=two_hand`: point with the dominant hand, tap thumb-to-finger on the
  other hand to click exactly where aimed, with no cursor drift and no lag; drag
  works by holding the click pinch while the pointer hand moves.
- `click_mode=single`: identical to today; all existing tests green.
- Mode switch is live and clean (tracker reconfigured to 1/2 hands without a
  crash). Python + vitest green.
