# AirControl — Complete Instructions

This guide explains **every feature and exactly how to use it**. If anything in
the [README](README.md) was unclear, this page removes the doubt. Work top to
bottom the first time; after that, jump to the section you need.

Hand-shape icons (like ✋ or 🖖) appear only to help you picture a gesture.

## Contents

1. [Core idea: armed vs. disarmed](#1-core-idea-armed-vs-disarmed)
2. [First run and setup](#2-first-run-and-setup)
3. [Calibration, step by step](#3-calibration-step-by-step)
4. [Arming and pausing](#4-arming-and-pausing)
5. [Single-hand controls](#5-single-hand-controls)
6. [Two-hand mode](#6-two-hand-mode)
7. [Custom gestures — motions and poses](#7-custom-gestures--motions-and-poses)
8. [Mapping a gesture to an action](#8-mapping-a-gesture-to-an-action)
9. [How recognition decides (and how many gestures you can have)](#9-how-recognition-decides-and-how-many-gestures-you-can-have)
10. [Settings and tuning](#10-settings-and-tuning)
11. [The Airy companion](#11-the-airy-companion)
12. [Practice mode](#12-practice-mode)
13. [Managing your gestures](#13-managing-your-gestures)
14. [Troubleshooting](#14-troubleshooting)
15. [Privacy and your data](#15-privacy-and-your-data)

---

## 1. Core idea: armed vs. disarmed

AirControl is always in one of two states:

- **Disarmed** — the camera may be watching, but **no input is sent to Windows**.
  This is where it always starts, for safety.
- **Armed** — your gestures move the mouse, click, scroll, and run your mapped
  actions.

You switch between them deliberately with your hand (see
[Arming and pausing](#4-arming-and-pausing)). Nothing you do controls Windows
until you arm.

---

## 2. First run and setup

1. **Set up** by cloning the repository and running `setup.cmd` once — see
   [Install](README.md#install--clone-and-run) in the README. (A packaged
   installer is also available if you would rather not install Python and Node.)
2. **Open the app** — run `app.cmd`. A native desktop window opens (it is not a
   browser tab). If enabled, the Airy companion appears too.
3. **Consent notice** — on first launch you must accept a one-time notice about
   MediaPipe's on-device tracking. AirControl will not start until you accept.
   (See [Privacy](#15-privacy-and-your-data) to withdraw consent later.)
4. **Turn on the camera** in the app if it is not already active. You should see
   a live preview with a hand skeleton drawn on your hand.
5. **Calibrate** before first real use (next section).

---

## 3. Calibration, step by step

Open the **Calibration** screen and press **Start calibration**. The whole flow
runs right there in the app, with a live camera preview and step-by-step
instructions — no terminal or extra window needed. Calibration teaches
AirControl your hand and your space so arming and gestures feel right. Each
step:

| Step | What to do | Why |
|---|---|---|
| **Framing** | Position your hand so wrist and fingertips stay in view, then press **Continue** | Ensures the whole hand is tracked |
| **Hand size** | Hold your hand at a comfortable working distance | Normalizes distances so nearer does not mean more sensitive |
| **Motion speed** | Move your hand naturally a few times when asked, then press **Continue** | Sets the motion thresholds to your pace |
| **Normal-work capture** | Just work normally for ~30 seconds | Learns your idle desk motion so it is ignored |
| **Lighting check** | Keep the scene lit, then press **Continue** to finish | Confirms tracking will be stable |

Steps that sample automatically (hand size, normal-work capture) just show
their progress — no click needed. Steps that wait for you show a **Continue**
button; **Cancel** is available at any time. The saved result is your **active
profile**. You can recalibrate any time from the same screen; the newest
profile becomes active.

---

## 4. Arming and pausing

- **Arm:** with your **dominant hand**, hold an **open palm** ✋ facing the
  camera (fingers together) until it arms. Hold it steady — there is a short
  "arm hold" dwell so it never arms by accident.
- **Pause (disarm):** hold a **fist** ✊ until it pauses. In two-hand mode, only
  your **dominant** hand's fist disarms.
- **Automatic pause:** if the hand is lost, the camera stalls, or you keep your
  hand away for a few seconds, AirControl pauses itself. A held drag is released
  after a short grace period.

You can also click the **Airy** widget to toggle armed/paused (see
[The Airy companion](#11-the-airy-companion)).

The arm and pause hold times are adjustable in **Advanced tuning**.

---

## 5. Single-hand controls

Once armed, with one hand:

| Do this | Result |
|---|---|
| Hold an **open palm** ✋ (dominant hand) | Arm control |
| Hold a **fist** ✊ | Pause / cancel the current gesture |
| **Point** ☝️ with the index finger and move | Move the pointer, like a relative touchpad |
| **Pinch** 🤏 thumb to index (other fingers folded) | Press the mouse button. The pointer freezes as the pinch closes, so the click lands where you aimed |
| **Release** the pinch | Release the button. A quick pinch is a **click**; a pinch that moved is a **drag** |
| **Two fingers** ✌️ (index + middle), move up/down | Scroll |
| **Three fingers**, swipe **left / right** | Next / previous app |
| **Three fingers**, swipe **up** | Task View |
| **Three fingers**, swipe **down** | Show desktop |

Notes:

- Every pose must be held steady for a brief moment before it takes effect, so
  passing through a shape on the way to another does not fire it.
- Swipe distance is normalized by hand size — moving closer to the camera does
  not make swipes hyper-sensitive.
- A three-finger swipe fires once; you must release before it can fire again.

---

## 6. Two-hand mode

Two-hand mode makes clicks and drags **explicit and deliberate**, which many
people find more precise.

**Turn it on:** Settings > Response > **Click mode > Two-hand**. (Set your
**Dominant hand** there too.)

How it works: your **dominant hand always points and does all the pinching**.
Your **other hand is a modifier** that decides what the dominant pinch means:

| Non-dominant hand | Then a dominant-hand pinch… |
|---|---|
| **Open palm** ✋ | **Locks** the cursor where you are pointing and **left-clicks** exactly there |
| **Fist** ✊ | **Holds** the button down, so moving = **drag** |
| Nothing / relaxed | Does **nothing** — so you never click by accident |

- Only the **dominant** hand's fist ✊ disarms. Your modifier hand can make a
  fist for dragging without ever pausing control.
- Custom gestures still work in two-hand mode — perform them with your dominant
  (pointing) hand.

---

## 7. Custom gestures — motions and poses

You can teach AirControl your own gestures. There are **two kinds**, and you
choose which when you record:

- **Motion gesture** — a movement, e.g. drawing a circle or an "L".
- **Hand pose** — a distinct **held shape**, e.g. Spock 🖖 or horns 🤘. No
  movement needed; you just hold it.

### 7a. Start a recording

1. Open the **Gestures** screen and click **+ Add Gesture** (or run
   `record.cmd`).
2. **Name it** — use a name for the *shape/motion*, not the action (e.g.
   "Circle" or "Spock"), so it still makes sense if you remap it later.
3. **Pick the type** — **Motion gesture** or **Hand pose**.

### 7b. Record the takes — you are in control

You capture each example on purpose; nothing is recorded until you ask for it.

1. Press **Record take** (or the **spacebar**).
2. A **3 - 2 - 1 countdown** gives you time to get into position.
3. Then:
   - **Motion:** the window shows **PERFORM NOW** — "Press stop when you finish
     the movement." Do the movement, then **press again to stop**. The take is
     auto-trimmed to your movement, so it works whether you move fast or slow.
   - **Pose:** the window shows **HOLD THE POSE STEADY** — "Press stop once you
     have held the pose steady." Hold your shape still until the meter is
     happy, then **press to capture**.
4. Review the take and choose **Keep** or **Discard**.
5. Repeat until you have enough. You need **3 to 5** kept takes: save as soon
   as you have 3, or add up to 5 for extra reliability. Once you have 5, the
   record control disappears — that's all this gesture needs.

**About the timer.** While capturing, the elapsed time you see is just a
stopwatch, not a target to fill. The take is trimmed down to your actual
motion (or held pose) either way, so stopping at 3 seconds and letting it run
to 10 produce the same exemplar. The 10-second limit shown alongside it is
only a **backstop** for when you forget to press stop — press stop as soon as
you're done. In fact stopping promptly is preferred: a shorter capture window
is less likely to catch your hand starting to move back toward the keyboard.

**Vary your takes a little.** Like a phone's fingerprint scanner asking for a
few presses at slightly different angles, AirControl matches by comparing to
whichever of your takes is closest — so a few takes that span a small, natural
range of angle, distance, or hand position give it a better sense of how you
really perform the gesture than several identical copies would. Don't
overdo it, though: keep performing the *same* gesture. Takes that vary too
much still fail the consistency check below.

Tip: in two-hand-ish setups, press the spacebar with your **other** hand so your
gesture hand never leaves the frame.

### 7c. Honest checks when you save

When you save, AirControl verifies the gesture is reliable and **refuses with a
plain reason** if not — without wasting a take. What each message means:

| Message | Meaning | Fix |
|---|---|---|
| "Your takes were too inconsistent — try again." | Your examples varied too much | Perform it the same way each time; re-record the odd ones |
| "Too similar to \<name\> — make this motion more distinct." | It clashes with an existing gesture | Choose a more different shape/motion |
| "This looks like normal desk motion — make it more distinct." | It resembles your idle movement (from calibration) | Make it more deliberate/unusual |
| "Record a few more takes before saving." | Fewer than 3 kept takes | Keep more takes (up to 5) |
| "Hold the pose steady." *(poses)* | Your hand drifted during the hold | Hold more still; brace your elbow |
| "Too similar to a built-in pose — pick a more distinct shape." *(poses)* | The shape matches a reserved built-in (open palm, fist, point, pinch, two-finger, three-finger) | Pick a shape that is not one of those |

### 7d. Good pose choices

Built-in poses are **reserved** because they already control the app:

- ✋ open palm (arm), ✊ fist (pause), ☝️ point (move), 🤏 pinch (click),
  ✌️ two fingers (scroll), three fingers (window actions).

So pick poses that are clearly **not** those. Reliable choices:

- 🖖 Spock (split between middle and ring fingers)
- 🤘 horns (index + pinky up)
- 🤙 shaka (thumb + pinky)
- 👌 "OK" ring (thumb + index circle)

### 7e. After saving

Saving closes the recording dialog and takes you **straight to mapping** the
new gesture (next section). Until you map it, a recorded gesture does
nothing — this is by design.

---

## 8. Mapping a gesture to an action

On the **Gestures** screen, each gesture has an action selector. Two ways to map:

1. **Curated verb** — pick from categories such as Media, Windows, Reading and
   browser, and more. These are safe, navigation-style actions.
2. **Shortcut recorder** — choose "record a shortcut", then press any key
   combination (e.g. `Ctrl + Shift + T`). AirControl turns it into a hotkey the
   gesture will send.

You can **enable/disable** a mapping without deleting it, and **remap** a gesture
at any time. Right after recording, the app highlights the new gesture and asks
you to choose what it does, so nothing is left unmapped by accident.

For safety, AirControl ignores a few dangerous hotkeys (like force-quit or
lock-screen combos) unless explicitly allowed in configuration.

### 8a. Test it — mandatory, and it's the real thing

The moment you assign an action to a freshly recorded gesture, AirControl opens
a **test dialog** for it. This is not a simulation: performing the gesture
here really dispatches the mapped action, exactly as it would during normal
use — so you see it actually happen (a window really switches, a track really
skips) rather than a synthetic "would have matched" message.

1. **Perform your gesture** (or **hold your pose**) in front of the camera,
   watching the live preview.
2. AirControl tells you what is happening as you go: no hand in view, hold
   still, holding steady, or the result of an attempt — a near-miss, a match
   with a *different* gesture, or a clean recognition that names the action it
   just performed.
3. **One successful recognition passes** the test — repeating it further would
   just repeat the action for no reason (imagine it were a volume or window
   change). A **Done** button appears; select it to finish.

If it does not test well, you are never trapped:

- **Try again** — resets the diagnostics without leaving the test.
- **Re-record this gesture** — deletes it and reopens recording so you can
  capture cleaner takes; your mapping choice is gone with it, but nothing else
  is disturbed.
- **Skip for now** — appears after a few unsuccessful attempts. Your mapping is
  already saved, so skipping never loses your work; you can always test it
  again later by reassigning its action.

---

## 9. How recognition decides (and how many gestures you can have)

AirControl matches what you do against **your own recordings** — not a fixed
alphabet. It finds the closest of your gestures and fires **only** when the match
is strong and clearly beats the runner-up.

It compares:

| Signal | Tells apart |
|---|---|
| Overall hand shape, and for motions the path over time | Different movements and shapes |
| **Palm orientation** — facing toward vs away from the camera | The same shape shown two ways |
| **Finger spread** — gaps between fingertips | A Spock split 🖖 vs a flat palm ✋ |
| **Finger fold** — how curled each finger is | Extended vs tucked fingers |

A custom gesture fires only at **≥ 85% similarity**. Motions and poses never
cross-fire: a held shape cannot trigger a movement gesture, and vice-versa.

### How many custom gestures can you create?

**There is no fixed limit.** You can add as many as you like — the only hard rule
is that each must have a **unique name**. In practice, a few things keep the
number sensible:

- **Distinctness:** every new gesture must pass the confusability check against
  the ones you already have, so they must stay meaningfully different. Poses are
  naturally limited by how many clearly different hand shapes you can hold.
- **Reliability:** the more similar two gestures are, the harder the ≥85% match
  and margin are to satisfy — keep them distinct and recognition stays crisp.
- **Performance:** while armed, AirControl compares each attempt against your
  whole library every frame, so a very large set uses more CPU.

A practical, comfortable library is roughly a dozen or two distinct gestures;
you can go further, but favour distinct shapes over many similar ones.

---

## 10. Settings and tuning

**Settings > Response** (everyday controls):

- **Pointer responsiveness / cursor speed** — how fast the cursor tracks your
  hand.
- **Dominant hand** — left or right; sets which hand points and which disarms.
- **Click mode** — Single-hand or Two-hand (see [section 6](#6-two-hand-mode)).

**Advanced tuning** (fine control; has a **Reset to defaults**):

| Setting | What it changes |
|---|---|
| Click engage distance | How close the pinch must be to press |
| Click release distance | How far the pinch must open to release |
| Pinch approach distance | When the pointer freezes as a pinch closes |
| Drag lock / release motion | Two-hand drag hold and how far it must move to start dragging |
| Arm hold time | How long to hold open palm ✋ before it arms |
| Pause hold time | How long to hold a fist ✊ before it pauses |
| Scroll speed | Notches per hand travel |
| Swipe distance | How far a three-finger swipe must travel |

**Calibration screen** — shows your active profile and lets you run guided
calibration again, in-app (see [section 3](#3-calibration-step-by-step)).

---

## 11. The Airy companion

Airy is a small, frameless, always-on-top widget that shows what AirControl is
doing and reconnects automatically. Its states:

- **Active** — connected and armed ("Tracking your gestures").
- **Inactive** — connected but paused ("Gesture tracking paused").
- **Offline** — AirControl is unreachable ("AirControl is not running").

Controls:

- **Click Airy** to arm or pause.
- **Drag** it to move (position is remembered).
- Click the small **×** to hide it.
- Run it on its own with `python -m aircontrol --widget`.

Enable/disable Airy and its size/behaviour in Settings.

---

## 12. Practice mode

Practice mode recognizes gestures and shows the action it **would** perform, but
never sends real input to Windows — perfect for learning or testing a new
gesture safely.

- Launch with `practice.cmd`, or open the app with practice mode
  (`python -m aircontrol --app --practice`).
- Use `arena.cmd` to practice your recorded gestures; pass `stress` to run the
  false-fire test that checks your gestures don't trigger accidentally.

---

## 13. Managing your gestures

On the **Gestures** screen you can:

- **Rename** a gesture.
- **Enable / disable** its mapping (keep the recording, stop it firing).
- **Delete** a gesture and its recordings.
- **Re-record** (delete and add again) — recommended if a gesture was recorded
  before you were comfortable with the flow, or feels unreliable.

---

## 14. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| It won't arm | Face your **open palm** ✋ to the camera with fingers **together** and hold longer. A splayed hand is treated as a custom shape, not the arm pose. Check lighting and that your whole hand is in frame. |
| A pose won't record ("too similar to a built-in") | Your shape falls into a reserved built-in (open palm/fist/point/pinch/two/three fingers). Pick a distinct shape like 🖖 🤘 🤙 👌. |
| A pose won't record ("hold the pose steady") | Your hand drifted. Brace your elbow and hold still until the steadiness meter is satisfied. |
| A custom gesture rarely fires | Re-record it with **consistent** takes; make it more **distinct** from your other gestures; keep the same palm orientation you recorded. Recognition needs a ≥85% match. |
| Too many accidental actions | Make gestures more distinct from each other and from normal movement; recalibrate the normal-work step; consider two-hand mode for clicks. |
| Cursor drifts or is jittery | Improve lighting; recalibrate hand size; lower cursor speed / raise smoothing in Settings. |
| Camera won't start / stalls | Make sure no other app is using the webcam; toggle the camera off/on in the app; the watchdog auto-pauses on a stalled feed. |
| Nothing controls Windows | You may be **disarmed** (arm with open palm ✋) or in **practice mode** (which never sends input). |
| Elevated/admin windows ignore it | Windows blocks input injection into elevated windows and secure screens. AirControl does not bypass that. |

---

## 15. Privacy and your data

- All tracking is **on-device**. Camera video is never uploaded, streamed, or
  saved. Only the 21 hand-skeleton landmarks drive recognition.
- The preview you see is annotated JPEG frames sent in RAM over the local
  `127.0.0.1` loopback only, for the UI — never written to disk or sent off the
  machine.
- Your calibration profile, gesture recordings (landmark trajectories only), and
  action mappings live in a local database under `%LOCALAPPDATA%\AirControl`.
- **Delete everything:** Settings has a delete-everything action that removes all
  of it.
- **Withdraw consent:** delete `%LOCALAPPDATA%\AirControl\.aircontrol-consent.json`
  (installed app) or `.aircontrol-consent.json` beside the active config file
  (when running from a clone). The consent notice reappears on next launch.
- Default actions are navigation-only — there are no delete, close, send,
  purchase, or shell gestures out of the box.

---

Still stuck? Open an issue on the
[project page](https://github.com/rajdeeepm/AirControl).
