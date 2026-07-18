# Two-Hand Interaction Redesign: Modifier-Hand Model (2026-07-18)

## Why
What was built (non-dominant pinch = click, non-dominant fist = drag) does not
match the user's intended design. The intended model keeps ALL pinching on the
dominant hand and uses the non-dominant hand as a MODE modifier.

## The intended model (user-specified)
In click_mode="two_hand", with the dominant hand as pointer:

| Non-dominant (modifier) hand | Dominant hand | Result |
|---|---|---|
| OPEN PALM held | pointing | cursor FREEZES (locked at aim point) |
| OPEN PALM held | pinch → release | LEFT CLICK at the locked position |
| FIST held | pinch + move | DRAG (button down, cursor follows) |
| FIST held | release pinch | drop (LEFT_UP) |
| absent / neutral pose | pointing | cursor moves normally |
| absent / neutral pose | pinch | NOTHING (no click without a modifier) |
| — | FIST (dominant) | disarm (clutch, unchanged) |

Key properties:
- Only the DOMINANT fist disarms. The non-dominant fist is only the drag
  modifier and must never touch the clutch.
- The non-dominant hand's pinch does nothing (it is not a click hand anymore).
- Click accuracy comes from the open-palm lock: the cursor cannot drift during
  the dominant pinch because movement is frozen by the modifier.
- Drag responsiveness comes from fist mode: dominant pinch-and-move drags
  immediately (single-mode pinch mechanics, approach-freeze/dead-zone relaxed
  or kept minimal).

## Implementation sketch
- engine: suppress_pinch_click becomes a mutable per-frame flag; pipeline sets
  it from the modifier state (suppress when modifier is neutral/absent; allow
  when OPEN_PALM lock or FIST drag mode).
- pipeline.process_hands (two-hand path):
  1. roles: pointer = dominant side (spatial, unchanged); modifier = other.
  2. read modifier pose: OPEN_PALM → "lock"; FIST → "drag"; else "neutral".
  3. lock: filter out MOVE_POINTER from pointer-engine output (freeze), allow
     pinch click (engine emits LEFT_DOWN/LEFT_UP as in single mode).
  4. drag: engine default pinch behavior (LEFT_DOWN + movement = drag).
  5. neutral: movement flows; pinch suppressed (no click).
  6. Remove the previous click-hand state machine (its pinch-click and
     fist-drag paths). Keep all safety releases (engine already owns the held
     button, so hand-loss/disarm releases apply automatically).
- Modifier debounce: reuse pose stability (a brief flicker of the modifier pose
  must not toggle modes mid-pinch; once LEFT_DOWN is held, keep the active mode
  until the pinch releases).
- Single-hand mode: completely unchanged.

## Tests (rewrite tests/test_two_hand.py click/drag sections)
- lock+click: modifier open palm, pointer moves → no MOVE_POINTER; dominant
  quick pinch → one LEFT_DOWN + LEFT_UP; cursor never moved during the click.
- drag: modifier fist, dominant pinch then move → LEFT_DOWN, MOVE_POINTERs
  while pinched, LEFT_UP on release.
- neutral: dominant pinch with no modifier → NO click.
- non-dominant fist alone → no disarm, no click.
- dominant fist → disarm (clutch) still works.
- mid-drag modifier flicker does not drop the drag; releases on hand loss /
  disarm / mode change still fire.
