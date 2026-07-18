# Live Bug Fixes Plan (2026-07-18)

Four issues from live testing. Executed by Codex, reviewed/committed by Claude.

## Fix 1 — Handedness inverted → wrong dominant hand (#1) and drag disarms (#3)
**Root cause:** the frame is mirrored before MediaPipe (selfie view), and two-hand
role assignment (`Pipeline._pointer_hand_index` / click-index) trusts MediaPipe's
handedness *label*, which comes out inverted for the user. So the dominant hand is
assigned to the wrong hand; the left-hand fist is then treated as the *pointer*
hand's fist and triggers the clutch disarm instead of a drag.
**Fix:** assign pointer/click roles **spatially in the mirrored preview the user
sees**, not by handedness label. dominant_hand="right" → pointer = the hand with
the larger center-x (right side of the mirrored image); "left" → smaller center-x;
click hand = the other. Deterministic tiebreak by confidence. This matches what the
user sees and is immune to MediaPipe label quirks. Also correct any displayed
handedness label to match the mirror. Result: dominant (right) hand points, left
hand fist = drag (not disarm). Tests in test_two_hand.py updated to spatial roles.

## Fix 2 — Recording shows no skeleton lines and captures no takes (#2)
**Investigate + fix:** during a recording session (a) ensure the camera preview is
enabled and the streamed preview frames include the **skeleton overlay** (the "no
lines"); (b) ensure the tracked hand's frame is actually fed into the
RecordingSession each tick and that **segmentation detects takes** (pending_take
events reach the UI); (c) confirm start_recording requires/uses the active
calibration profile's motion signature. Net: the recording dialog shows the live
hand skeleton and captures takes that the user can Keep/Discard.

## Fix 3 — Advanced tuning not visible where the user expects (#4)
The advanced tuning knobs were added to the Settings screen but the user asked for
them in the **Calibration** area and cannot find them. **Fix:** surface the
Advanced tuning controls in the Calibration screen (and verify they actually render
— not hidden by a collapse/guard bug). Keep them working (live set_app_setting +
reset).

## Order
Fix 1 (breaks core control) → Fix 2 (Add Gesture unusable) → Fix 3 (discoverability).
Single-hand mode and all existing tests stay green throughout.
