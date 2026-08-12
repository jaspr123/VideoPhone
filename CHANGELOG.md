# Changelog

## Unreleased — DEVELOPER: "Find receiver switch" wizard

Real-hardware feedback from the first Pi test of the Settings/Developer
screens: the receiver hook switch wasn't registering lift/hang-up at all,
and the DEVELOPER screen's Receiver card never changed either -- consistent
with either the switch being wired to a different GPIO pin than
`hook_switch_gpio_pin`, or being a normally-closed switch (reading the
opposite of what the code assumed).

- **`hardware/hook_switch.py`**: `HookSwitch` gained an `invert` parameter
  (default `False`, matching the previously-hardcoded normally-open
  assumption). Flips `is_lifted`'s interpretation for a normally-closed
  switch wired the same GND-only way the spec requires -- this is a
  different axis from *which pin* it's on, and both can be wrong
  independently.
- **`config.py`**: new `hook_switch_invert` field (validated bool,
  default `False`), and `hook_switch_gpio_pin`/`hook_switch_invert` added
  to `EDITABLE_SETTINGS_KEYS` so the wizard below can persist through the
  same validate-before-write `BoothConfig.save_settings` path SETTINGS
  already uses, rather than a separate one.
- **New DEVELOPER control, "Find switch"** (next to the Receiver card):
  opens a live-monitor wizard rather than having software guess a pin/
  polarity from timing windows it has no way to verify without the real
  receiver in front of it:
  1. Releases the app's own hook switch and opens every candidate GPIO
     pin (`main.py`'s `HOOK_SCAN_CANDIDATE_PINS` -- general-purpose pins
     only, excluding I2C/UART/EEPROM-ID pins even though this project
     doesn't use them, since scanning those risks interfering with
     something else on the board) so their live raw states can be shown
     side by side (`ui/renderer.py`'s `render_hook_scan`).
  2. Attendant lifts/hangs up the receiver while watching the grid and
     taps whichever pin's dot actually responds.
  3. Attendant lifts the receiver, holds it up, and taps Confirm -- this
     one physical-position confirmation is what determines normally-open
     vs normally-closed, replacing any timing heuristic entirely.
  4. Apply saves both fields and reconnects the hook switch live, no
     restart needed; Cancel at any point restores the original pin
     untouched.
  - A pin gpiozero can't claim is shown as "unavailable" rather than
    aborting the whole scan; if gpiozero itself isn't installed, or every
    candidate pin fails to claim, the wizard shows a clear error instead
    of a blank/broken grid.
- Added `tests/unit/test_hook_switch.py` invert coverage,
  `test_config.py` coverage for the new field/editable keys, and a full
  `test_main.py` suite for the wizard's state machine (start/select/
  confirm/retry/apply/cancel, teardown on leaving DEVELOPER mid-scan,
  render/touch dispatch) using a fake gpiozero `Button` shared between
  `main.py` and `hardware/hook_switch.py`'s references to it. 380 tests
  pass.
- **Still not verified on the actual switch** -- this fixes what could be
  fixed without seeing the real symptom (pin/polarity mismatch is by far
  the most common cause of "switch doesn't work" reports), but if the
  wizard finds no responding pin at all, that points at wiring/hardware,
  not something the software can resolve.

## Unreleased — Guest-screen visual refresh + attendant Settings/Developer screens

Implements the Claude Design handoff (`9 Developer.dc.html`, `1 Welcome.dc.html`,
and the rest of the 9-screen "Guestbook Kiosk" mockup set) end to end:
brings the six existing guest screens' `ui/renderer.py` code up to date
with a long run of mockup-only edits that had never been ported back
(headline-over-preview, the horizontal sound-check meter, the tips column,
camera blur, the enlarged Tap to Record button, the unified single-line
header), applies the previously-proposed ERROR redesign and static-layer
caching from `renderer-patch-notes.md`, and adds two brand-new attendant
screens (SETTINGS, DEVELOPER) that didn't exist in the app at all before
this pass.

- **`ui/renderer.py`**: every screen's layout rewritten to match the final
  mockups pixel-for-pixel at 1024x576 (see the mockup files for the design
  source of truth):
  - Replaced the old vertical VU-style mic meter (`_camera_and_meter_row`,
    `_mic_meter`) with a full-width camera panel (`_camera_panel`), a
    right-hand tips column (`_tips_column`: camera/mic/smile icons, reused
    from the old COUNTDOWN screen), and a horizontal sound-check bar
    (`_sound_check_bar`) pinned at the same (36,444,824,~50) coordinates on
    GET READY, COUNTDOWN and RECORDING so it holds still as the guest
    moves between them.
  - `_couple_header_bar`: the compact "NAMES · gold hairline · DATE" row
    now shared by GET READY, COUNTDOWN, RECORDING, SAVING, SAVED and
    ERROR (previously SAVING/SAVED/ERROR had their own two-line stacked
    header, or none at all). Nudged to y=46px (from the design's y=24px)
    because this theme's actual frame art has corner foliage that clips
    "2024" at y=24 -- the mockup's stand-in background photo didn't have
    that problem.
  - WELCOME (`render_ready`): fixed content-column centering (374px) and a
    fixed couple-photo panel (700,61,300,408), replacing the old
    photo-width-dependent layout math.
  - COUNTDOWN now shows the (blurred) live/frozen camera feed behind the
    ring -- previously it showed no camera at all, despite
    state_machine.py's own docstring already documenting that the feed is
    genuinely live at that point. `render_countdown` and `main.py`'s
    `render()` dispatch were updated to pass a camera frame through.
  - RECORDING rebuilt on the GET READY base per the design brief ("make
    the record page look just like the get ready page"): blurred still
    frame, "Now Recording" overlay, status pill with only the remaining
    time (elapsed dropped -- the design no longer shows it), and a new
    **Cancel & restart** button.
  - ERROR: applied `renderer-patch-notes.md`'s proposed redesign (gold
    divider + a bounded, word-wrapped cream card for the reason string via
    the new module-level `_wrap_text`), plus the header bar.
  - **Static-layer caching** (`_static_layer`, per the patch notes):
    WELCOME, SAVING, SAVED and ERROR now render their non-animated chrome
    once and reuse a cached copy every frame, redrawing only the
    footer-diamond pulse / spinner arc / checkmark stroke-on / (nothing,
    for ERROR) on top. GET READY, COUNTDOWN and RECORDING are left
    uncached, as the patch notes suggested -- the live/blurred camera
    panel dominates their per-frame cost either way. **Not perf-tested**:
    no Pi or real camera in this sandbox. On-device checklist: run the
    current build, compare PREVIEW/COUNTDOWN/RECORDING/WELCOME frame
    pacing against a pre-change build; also watch ERROR's static-layer
    cache size in a long-running session (it's keyed on the reason
    string, so a validation/recorder failure message that embeds
    per-instance detail, e.g. a timestamp, would grow it unboundedly --
    today's actual failure reasons are a small fixed set of strings, so
    this is believed fine, but wasn't exercised against real failure
    traffic).
- **New `SETTINGS` and `DEVELOPER` states** (`state_machine.py`):
  `READY <-> SETTINGS <-> DEVELOPER`, reachable only from `READY` and
  never part of `GUEST_INPUT_STATES`. Also added `RECORDING -> PREVIEW`
  for the new Cancel & restart action.
- **Admin entry point**: holding the bottom-right 90x90px corner of the
  READY screen for 1.6s opens SETTINGS (`main.py`'s
  `ADMIN_CORNER_SIZE`/`ADMIN_LONG_PRESS_SECONDS`, `_handle_mouse_down`/
  `_handle_mouse_up`). This was the one open design question the mockup
  chat never got an answer to; picked long-press over a keyboard shortcut
  since the physical booth has no keyboard exposed to guests. **Untested
  against a real touchscreen's reported coordinates** -- same caveat the
  original PREVIEW record-button hit-testing already carried.
- **`config.py`**: new `extended_mode` field (Guestbook 90s / Extended
  300s presets, `GUESTBOOK_MAX_RECORDING_SECONDS` /
  `EXTENDED_MAX_RECORDING_SECONDS`) and `BoothConfig.save_settings()` --
  validates a patch of `EDITABLE_SETTINGS_KEYS` against the full config
  schema *before* writing, then writes atomically (temp file + rename) so
  a bad attendant edit can never corrupt `booth.default.json`.
- **`main.py`**: SETTINGS wires the message-length/countdown steppers,
  Quality/Compat and Guestbook/Extended segmented controls, and the live
  mic meter toggle to real config reads/writes via `_apply_settings_action`
  / `_save_settings`, applied immediately (no restart needed). "Create new
  event", "Change event" and "Test recording" are intentionally **UI-only
  placeholders** -- there's no multi-event config schema yet
  (PROJECT_SPEC.md section 10 is an explicitly later milestone); tapping
  them just logs and no-ops. DEVELOPER's "Export logs" and "Restart booth"
  are the same: logged, not implemented. `_cancel_recording` stops
  ffmpeg, deletes the discarded file, and returns to PREVIEW.
  `_developer_snapshot` gathers camera fps/dropped-frame counts (new
  rolling counters), mic peak/room-base/clip-count (reusing the existing
  `AudioLevelReader` mic-check machinery, started/stopped on
  DEVELOPER entry/exit), hook-switch state (already polled every frame),
  and CPU temp/throttling (`vcgencmd`), CPU load/memory (`/proc`), disk
  free (`shutil.disk_usage`), encoder status and uptime -- all
  **best-effort and Linux/Pi-specific**, degrading to `None`/"n/a" off
  that platform or without `vcgencmd`, never raising.
- Added/updated `tests/unit/test_renderer.py`, `test_state_machine.py`,
  `test_config.py`, `test_main.py` for every signature change above plus
  new coverage (SETTINGS/DEVELOPER rendering and rect population, the
  admin long-press flow, settings persistence round-trips including
  rejected/invalid edits, cancel & restart, `_developer_snapshot`'s
  shape). Full suite (348 tests) passes.

## Unreleased — New PREVIEW screen: live camera before recording, not during

User's idea after three rounds of RECORDING-time live-preview trouble: "what
if we convert the get ready page as a preview page like the record page but
with a live preview so someone can ready then have a record button push to
start. then count down then switch to record page with screenshot of last
frame, and look here and hang up when your done." This is a genuine
architecture fix, not a workaround -- it removes the root cause of the
audio-breakup/performance problems chased in the three preceding entries
below, instead of further mitigating them.

- **New `PREVIEW` state** (`state_machine.py`), between `READY` and
  `COUNTDOWN`: `READY -> PREVIEW -> COUNTDOWN -> RECORDING -> ...`. The
  guest lifts the receiver into `PREVIEW`, not straight into `COUNTDOWN` as
  before.
- **Why this is safe where the old approach wasn't:** ffmpeg hasn't opened
  the camera yet during `PREVIEW` (or `COUNTDOWN`) -- `main.py`'s
  `self.capture` (the same OpenCV preview object every pre-recording screen
  already reads from) is still open, exactly as it always has been on
  `COUNTDOWN`. So `PREVIEW` shows a genuinely live feed with **zero** new
  processes, zero extra ffmpeg outputs, zero risk to the recording's audio
  pipeline. `RECORDING` goes back to showing exactly what it always showed
  before the removed feature existed: the last frame captured before ffmpeg
  took over, now with a "look here, hang up when you're done" caption over
  it (new `recording_look_here` theme text key).
- **Removed the RECORDING-time live video preview entirely**
  (`live_preview_enabled` and everything under it): `media/ffmpeg.py`'s
  second `-map 0:v:0` / `-update 1` JPEG output, `RecordingSession.
  live_preview_path`, `Recorder`'s live-preview file handling, and
  `main.py`'s `_read_live_preview_frame()`. This was the actual source of
  every RECORDING-preview hardware complaint this project has had; moving
  the live feed to before `RECORDING` starts removes the conflict instead
  of continuing to chase it.
- **Tap-to-record with a safety-net timer, not a hard requirement:**
  `PREVIEW` shows a pulsing on-screen "Tap to Record" button
  (`Renderer.render_preview()`, hit-tested in `main.py`'s new `_on_mouse()`
  via `cv2.setMouseCallback`) but also auto-advances into `COUNTDOWN` after
  `preview_seconds` (new config field, default `8`) if nobody taps -- a
  missed or miscalibrated touchscreen tap on real hardware (not yet
  verified) can never strand a guest. Spacebar is an equivalent trigger in
  `PREVIEW`, same as it already was for every other guest action
  (architecture rule 11).
- **`main.py`:** `_start_countdown()` (hook-lift entry point) split into
  `_start_preview()` (enters `PREVIEW`, starts the mic-check thread, plays
  the pickup greeting, sets the `preview_seconds` deadline) and a new,
  narrower `_start_countdown()` (entered by a tap/Spacebar/auto-advance
  from `PREVIEW`; just starts the numeric countdown -- the mic check keeps
  running uninterrupted from `_start_preview()`). `_cancel_countdown()`
  generalized to `_cancel_to_ready()`, now handling a hang-up from either
  `PREVIEW` or `COUNTDOWN`.
- **`ui/renderer.py`:** extracted the bordered/mirrored camera panel + mic
  meter column (previously inline in `render_recording()`) into a shared
  `_camera_and_meter_row()`, now used by both `render_recording()` and the
  new `render_preview()`.
- **Config:** new `preview_seconds: int` (default `8`), `live_preview_enabled`
  removed. `live_mic_meter_enabled` (RECORDING's mic meter, unrelated to the
  removed video preview) is unchanged.
- **Theme:** new required `text` keys `preview_caption`, `preview_button`,
  `preview_footer`, `recording_look_here`; `themes/classic-walnut/theme.json`
  updated. Existing themes must add these four keys or `Theme.load()` will
  reject them at startup, same as any other missing required text key.
- All corresponding tests added/updated across `test_state_machine.py`,
  `test_config.py`, `test_ffmpeg.py`, `test_recorder.py`, `test_renderer.py`,
  and `test_main.py` (new coverage for `_start_preview`/`_start_countdown`/
  `_cancel_to_ready`, the `preview_seconds` auto-advance in `tick()`, and
  `_on_mouse()`'s button hit-testing). Full suite: 282 passed.
- **Not yet confirmed on real hardware**, in particular the touchscreen tap
  coordinates against `Renderer.record_button_rect` -- `preview_seconds` is
  the deliberate safety net for exactly that uncertainty.

## Unreleased — Split RECORDING mic meter into its own toggle, switch it to arecord

Direct hardware feedback: "the mic level isn't fluid... the mic is more
fluid in the get ready screen, we could just use that? add the
arecord-based reader but vertical on the record screen." Replaced the
`astats`/`ametadata` ffmpeg-tap mic mechanism (previous entry below) with
a reuse of the exact `AudioLevelReader`/`arecord` mechanism already proven
smooth on the Get Ready screen, and gave the mic meter its own config
toggle so it's no longer tied to the video preview toggle.

- **`config.py` / `config/booth.default.json`:** new independent
  `live_mic_meter_enabled: bool` field (default `false`), validated the
  same way as `live_preview_enabled` and fully decoupled from it -- either
  can be on, off, or mixed.
- **Removed the astats/ametadata mechanism entirely:**
  `media/ffmpeg.py`'s `build_record_command()` no longer takes a
  `live_level_path` parameter or extends the `-af` chain; `-af` is back to
  the plain `aresample=async=1:first_pts=0`. `media/audio_levels.py`'s
  `read_latest_live_level()` and its regex/tail-read constants are gone.
  `media/recorder.py`'s `RecordingSession` no longer carries a
  `live_level_path` field.
- **`main.py`:** generalized the existing countdown-time mic-check thread
  (`_start_mic_check`/`_mic_check_loop`) with a `context` label, a
  `reset_latest` flag, and a `start_delay_seconds` parameter, then reused
  it for RECORDING via a new `_start_recording_mic_meter()` call at the
  end of `_start_recording()`, stopped via the existing `_stop_mic_check()`
  at the start of `_stop_and_save()`.
  - `start_delay_seconds=1.0`: waits a second after ffmpeg's own `Popen`
    call before attempting to open the device, so ffmpeg always gets first
    claim on the ALSA device.
  - `reset_latest=False`: if the second `arecord` can't open the
    already-busy device, the meter keeps showing the last countdown
    reading instead of going blank for the whole recording -- graceful
    degradation to exactly the old frozen behavior, not a new failure
    mode.
  - The countdown-phase gain-nudge flow (`_apply_mic_gain_nudge`) is
    untouched and still only ever fed by the original countdown check --
    the recording-phase reader's readings are never used to adjust gain,
    preserving the rule that gain must not change once recording begins.
- **No rendering change needed:** the RECORDING screen's meter widget was
  already a vertical stacked bar; only the data source powering it
  changed.
- All corresponding tests updated/added across `test_config.py`,
  `test_ffmpeg.py`, `test_audio_levels.py`, `test_recorder.py`, and
  `test_main.py`. Full suite: 266 passed.
- **Not yet confirmed on real hardware** -- same as the mechanism it
  replaces, whether the second `arecord` actually gets the device depends
  on the specific ALSA driver/config; if it can't, the meter simply stays
  frozen at the last countdown reading rather than failing loudly.

## Unreleased — Fix live preview/mic meter perf bug, default it off

Real-hardware feedback on the just-shipped live RECORDING preview/mic
meter: the mic meter got choppier as a recording went on, and the preview
was "breaking" the recording (audio issues). Found and fixed the actual
bug rather than abandoning the feature, but defaulted it back off until
the fix is confirmed on real hardware.

- **Root cause:** the mic-level tap printed *all* ~20 `astats` metrics
  every 100ms into a file with no bound on size for the whole recording,
  and `main.py` re-read and re-parsed that *entire, ever-growing* file on
  every render tick -- cost that climbed the longer a recording ran,
  explaining both "not fluid" (gets worse over time) and the extra CPU
  load contributing to audio breakup.
- **Fix, not abandonment:**
  - `media/ffmpeg.py`: `astats` now computes only the 2 metrics actually
    used via `measure_perchannel=Peak_level+RMS_level:measure_overall=none`
    (restricting what gets *computed and attached* in the first place,
    not filtering at print time). Tried chaining two `ametadata=print`
    filters writing the same file first (one per key) to get the same
    effect -- that made ffmpeg hang, caught by testing against real ffmpeg
    before shipping, same as every other change to this file this
    session. Also shrank the preview JPEG (480px -> 320px) and slowed it
    (5fps -> 2fps) to cut its cost further, directly per the "take load
    off the rpi" ask.
  - `media/audio_levels.py`: `read_latest_live_level()` now only reads the
    last ~4KB of the level file (`_LIVE_LEVEL_TAIL_BYTES`), not the whole
    thing -- cost stays flat regardless of how long the recording has
    been running, instead of growing with it.
  - Re-validated against real ffmpeg end-to-end with the actual
    `build_record_command()` output (not a hand-typed approximation):
    confirmed the level file stays small and the parser reads it
    correctly straight from a real ffmpeg run.
- **Default flipped to off** (`live_preview_enabled: false`): this feature
  has now caused two rounds of real-hardware trouble in a row. The fix
  above is validated against real ffmpeg the same rigorous way everything
  in this file has been, but not yet confirmed on the actual Pi -- so it
  stays opt-in rather than defaulting back on. Flip it on in
  `config/booth.local.json`, test a real recording, and report back before
  it becomes the default again.
- **Answered: "the Get Ready screen's mic meter is fluid, can't we just
  reuse that during RECORDING too?"** No, not directly -- that reader is a
  live `arecord` process, and ffmpeg holds the ALSA capture device
  exclusively while actually recording, the same one-process-at-a-time
  constraint that governs the camera. A second `arecord` at the same time
  would either fail outright or depend on ALSA `dsnoop`/`dmix` sharing
  being configured on that specific hardware, not something to assume.
  The `astats` tap already in use gets the same live-level result without
  a second process, by reading the audio ffmpeg is already decoding for
  the recording itself.
- Updated existing tests for the new default and the narrowed `astats`
  filter string; added a default-value test. 269 tests passing.

## Unreleased — Genuinely live RECORDING preview/mic meter, hook switch debounce fix, welcome screen cleanup

Second round of real-hardware feedback: a visual glitch on the welcome
screen, a request to make the RECORDING screen's preview and mic meter
actually live (previously documented as an intentionally-frozen
limitation), and a hook switch that sometimes didn't register a lift or
needed a second hang-up motion to register.

- **Welcome screen:** removed the ambient "bokeh" effect (soft blurred
  drifting circles) from the `READY` screen -- reported as looking like a
  visual glitch on real hardware. It was the only animated effect unique
  to that screen, so removed outright rather than trying to retune it
  blind; `_draw_ambient_bokeh` and its `_soft_blur` helper are gone.

- **Hook switch double-action bug (real root cause found, not guessed):**
  `gpiozero`'s `Button(bounce_time=...)` only debounces its own
  `when_pressed`/`when_released` callback system -- it does *not* debounce
  plain `is_pressed` reads, which is all `HookSwitch.is_lifted` (and this
  app's polling loop) ever does. A tight poll loop reading raw GPIO state
  during a real mechanical switch's bounce window can see a transition
  "eaten" by bounce-parity, exactly matching the reported symptom (lift
  sometimes did nothing; hang-up sometimes needed a second motion).
  `hardware/hook_switch.py` adds `HookSwitch.poll(now)`: a raw reading must
  hold steady for `poll_debounce_seconds` (~200ms) before it's accepted as
  a real state change. `main.py`'s edge detection now calls `poll()`
  instead of the raw `is_lifted` property. 6 new tests simulate bounce
  sequences with controlled timestamps (no real GPIO or real time needed).

- **Live RECORDING preview + mic meter (previously a documented
  limitation, now real):** the recording ffmpeg process now writes two
  *extra*, lightweight outputs on top of the actual recording -- a
  continuously-overwritten low-res JPEG (`fps=5,scale=480:-2`, `-update 1`
  image2 muxer) and a periodic mic-level readout (an `astats`/`ametadata`
  tap appended to the *existing* audio filter chain, not a second capture).
  Neither adds a second process touching the camera or audio device, so
  the one-process-at-a-time constraints this project already fought
  through stay intact. New config `live_preview_enabled` (default `true`)
  turns both off at once if this ever needs to be ruled out as a cause of
  audio breakup.

  This was validated directly against a real `ffmpeg` binary before being
  wired into the app, not just unit-tested: dual-mapping the same input
  stream into both a `-c:v copy` output and a filtered low-fps output,
  combined with the proven `-thread_queue_size`/`-use_wallclock_as_
  timestamps` sync flags, initially appeared to break the second output
  ("No filtered frames for output stream") when tested against a
  pre-muxed test file -- root-caused to the test file's own embedded
  timestamps conflicting with forced wallclock timestamps, an artifact of
  the test method, not the technique. Re-validated against raw MJPEG/H.264
  *elementary streams* (no container timestamps, matching how `v4l2`/
  `alsa` actually deliver live frames) in every combination this app can
  produce (`quality`+MJPEG copy, `fast`+MJPEG live-encode, H.264 copy,
  `av_sync_offset_ms` itsoffset) and confirmed working every time. Finally
  re-verified against the actual `build_record_command()` output byte-for-
  byte (only the device source flags substituted, since this sandbox has
  no real camera), including the astats level-parsing.
  - `media/ffmpeg.py`: `build_record_command()` gains optional
    `live_preview_path`/`live_level_path` params; extends the *existing*
    `-af` chain for the level tap rather than adding a third output.
  - `media/audio_levels.py`: `read_latest_live_level()` parses ffmpeg's
    `ametadata=print` output (channel-1 Peak/RMS pair), tolerant of a
    read caught mid-write and of `-inf` (true silence).
  - `media/recorder.py`: `RecordingSession` gains `live_preview_path`/
    `live_level_path`; `Recorder.start()` clears stale files from the
    previous session before ffmpeg writes fresh ones.
  - `main.py`: `_read_live_preview_frame()`/`_read_live_level_reading()`
    poll these each render tick during `RECORDING`, skip re-decoding an
    unchanged file (by mtime), and fall back to the last good value on a
    transient read-mid-write -- never block, never raise.
  - 25 new tests across `test_ffmpeg.py`, `test_audio_levels.py`,
    `test_recorder.py`, and `test_main.py`. 264 tests passing.

## Unreleased — Pickup greeting sound + custom tip icons

Follow-up feedback after the themed-UI pass landed on real hardware: a
pickup greeting request, custom icon artwork for the countdown screen, and
two reports ("mic levels on the record screen weren't working", "neither
was the live preview") that turned out to be the RECORDING screen's
already-documented frozen-preview/frozen-meter limitation, not new bugs —
clarified in the README with a way to tell that apart from an actual mic
capture failure (check `logs/booth.log` for `countdown mic check` warnings).

- Added `src/video_guestbook/media/audio_playback.py`: fire-and-forget
  `aplay` playback (`play_sound_async`), centralized like every other
  subprocess call in `media/` -- never blocks, never raises (missing file,
  missing `aplay`, bad device are all logged and swallowed per rule 17).
- `config.py`: added `audio_playback_device` (optional ALSA *playback*
  device, e.g. for a handset speaker that isn't the system default;
  distinct from `audio_device`, the microphone).
- `ui/theme.py`: added two **optional** theme.json sections that fall back
  to built-in behavior when absent (unlike the required colors/fonts/text
  sections): `sounds` (currently just `pickup`, played when the receiver
  is lifted) and `icons` (override any of the countdown screen's `camera`/
  `mic`/`smile` tip icons with a transparent PNG instead of the built-in
  vector drawing -- any subset of the three keys works).
- `ui/renderer.py`: loads any theme-provided icon PNGs at startup and
  pastes them (aspect-preserving, no cropping) in place of the procedural
  icon for that tip; a missing/corrupt custom icon file falls back to the
  built-in vector icon rather than crashing.
- `main.py`: `_start_countdown()` (fired by both the hook switch and
  Spacebar, same as always) now also fires the theme's pickup sound if one
  is configured -- fully optional, silent no-op when the theme has none.
- Added `tests/unit/test_audio_playback.py` (mocked `aplay`/subprocess),
  extended `test_theme.py` (sounds/icons validation) and `test_config.py`
  (`audio_playback_device`), and added pickup-greeting integration tests to
  `test_main.py`. 234 tests passing.
- No default pickup sound or custom icon artwork is bundled -- both are
  fully optional and off until a theme supplies them, since the actual
  audio wording/voice and icon artwork are something the user wants to
  choose, not something to generate on their behalf without asking.

## Unreleased — Themed guest UI (Claude Design handoff)

Implements the "Guestbook Kiosk" visual design (a Claude Design HTML/CSS/JS
mockup, handed off for implementation) as a real, data-driven theme system
for all 6 booth screens, replacing the plain white-text-on-video overlays
from Milestone 1.

**Rendering approach: Pillow over OpenCV, not a browser.** The mockup was a
browser prototype (`getUserMedia()` camera, CSS keyframe animations, Web
Audio mic meter). Two options were considered for bringing it onto the Pi:
a Chromium kiosk browser (pixel-exact, but needs a second live video/audio
pipeline or the browser sharing the camera with ffmpeg -- both conflict with
the one-process-at-a-time V4L2/ALSA constraints already fought through this
project, and add a whole new real-time process competing for CPU on
hardware where encoder-vs-audio-capture contention already caused a real
bug this session), or recreating the design with Pillow inside the existing
OpenCV/ffmpeg pipeline (no new real-time processes, reuses the proven
camera/ffmpeg handoff, close-but-not-pixel-identical animations). Went with
the Pillow approach; the theme data (colors, fonts, copy, art) is structured
so a future browser-kiosk renderer could reuse it without redoing the
design work, if that's ever wanted.

- Added `themes/` -- a data-driven theme system (PROJECT_SPEC.md section
  11). `config/booth.default.json` gains a `theme_dir` field (default
  `themes/classic-walnut`).
  - `src/video_guestbook/ui/theme.py`: `Theme.load()` reads and fully
    validates `theme.json` (colors, font/asset file paths, required copy
    keys) up front, raising a clear `ThemeError` naming exactly what's
    wrong before the app opens its window.
  - `themes/classic-walnut/`: the shipped default theme -- `theme.json`,
    bundled `.ttf` fonts (Great Vibes + Cormorant Garamond, converted from
    Google Fonts' woff2 via `fonttools` so the kiosk has zero CDN/internet
    dependency at runtime), and the floral frame/couple-photo art from the
    design handoff.
- Added `src/video_guestbook/ui/renderer.py`: a `Renderer` that composites
  each of the 6 states (`READY`/`COUNTDOWN`/`RECORDING`/`SAVING`/`SAVED`/
  `ERROR`) as a full Pillow RGBA image -- floral border art, tracked-letter
  uppercase typography, an animated countdown ring + scale/fade numeral
  pop-in, a pulsing recording dot and footer-bar accents, a spinning
  "saving" ring, a progressively hand-drawn checkmark stroke on the thank-
  you screen, and a 13-segment color-graded mic level meter -- then
  flattens to a BGR array for `cv2.imshow`. Only `RECORDING` composites the
  live/frozen camera frame (mirrored, in a bordered panel); every other
  screen is fully synthetic branded art, matching the actual design file
  (which, contrary to an earlier draft brief, only shows the camera during
  the recording screen itself, not as a full-bleed background everywhere).
- Fixed a real bug caught via rendered-screenshot review, not just
  exceptions: `PIL.ImageDraw` does **not** alpha-composite when drawing
  directly onto an image -- a semi-transparent fill just overwrites the
  pixel's stored alpha value, so every intended "faded/unlit" element (mic
  meter off-segments, pulsing footer hearts, the recording dot's pulse, the
  countdown/saving ring's background track) was rendering at full opacity,
  silently breaking the animations and the meter's on/off legibility.
  Fixed with a `_blend_rgb`/`_blend_full` helper that pre-blends the
  intended color against its known local background before drawing.
- Fixed several vertical-layout overflow bugs (also only visible by
  rendering and inspecting actual screenshots, not from exceptions or unit
  tests alone): the countdown, saving, and thank-you screens' text/graphics
  budgets exceeded the canvas height, pushing the countdown ring and the
  saving/thank-you screens' final lines of text underneath the footer bar
  where they were invisible. Recomputed tighter, verified vertical budgets
  for all three screens.
- `config.py`: added and fully wired `theme_dir` (was initially added as a
  dataclass field only, without being read from the config JSON or resolved
  against `base_dir` -- caught by a regression test before it shipped;
  fixed to match the existing `output_dir`/`log_dir` pattern).
- `main.py`: `BoothApp` now takes a `Theme` and builds one `Renderer` at
  startup; `render()` dispatches to the themed per-state renderer instead
  of `cv2.putText` overlays. The countdown-time mic level check (already a
  real, working feature from a previous pass) now feeds the visible mic
  meter on the countdown screen; during `RECORDING`, ffmpeg holds the audio
  device exclusively (same constraint as the camera), so that screen's
  meter shows the last reading from the countdown check rather than a live
  one -- documented as a known limitation, not silently faked.
- Added `tests/unit/test_theme.py` and `tests/unit/test_renderer.py` (44
  new tests): theme validation (missing/invalid keys, missing files,
  conditional couple-photo requirement) using synthetic temp themes, plus a
  regression guard that loads the real bundled theme; renderer smoke tests
  for all 6 screens (correct output shape/dtype, no exceptions, various
  camera-frame aspect ratios, mic-fraction out-of-range clamping, the
  countdown numeral's pop-in state machine). 216 tests passing overall.
- **Known limitation, deferred on purpose:** the `RECORDING` screen's
  camera preview is the pre-existing frozen-last-frame behavior (ffmpeg
  owns the camera exclusively while recording, same as before this
  change) -- a true live feed during actual recording needs ffmpeg to fan
  out a second low-res preview stream, scoped as a distinct follow-up so it
  isn't stacked onto this change untested on real hardware.

## Unreleased — Milestone 1: Consolidate current Beta

- Initial repository structure created per PROJECT_SPEC.md section 14.
- Added `video_guestbook` Python package:
  - `config.py`: validated JSON booth configuration loading.
  - `state_machine.py`: READY/COUNTDOWN/RECORDING/SAVING/SAVED/ERROR state
    machine with guest-input gating during SAVING.
  - `logging_setup.py`: rotating file + console logging with session IDs.
  - `media/ffmpeg.py`: centralized ffmpeg/ffprobe command construction.
  - `media/recorder.py`: recording session start/stop (graceful `q` stdin
    stop), unique session ID and filename generation.
  - `media/validation.py`: post-recording validation via ffprobe (video +
    audio streams present, minimum duration, non-empty file).
  - `main.py`: full-screen OpenCV MJPEG preview, Spacebar start/stop,
    countdown, and state-driven UI overlay.
- Added `config/booth.default.json` using the stable ALSA device
  `plughw:CARD=Device,DEV=0`.
- Added `scripts/test_hardware.sh` (camera, microphone, ffmpeg/ffprobe,
  output directory checks) and `scripts/install.sh`.
- Added unit tests for state transitions, filename creation, configuration
  validation, and recording validation logic (`tests/unit/`, 60 tests).
- Added README with install/run instructions and manual acceptance tests.

## Unreleased — Fold in the confirmed-working prototype

- Added `legacy/booth.py`: a verbatim backup of the actual working Beta
  prototype, confirmed running on the Raspberry Pi hardware.
- Fixed a camera/ffmpeg resource conflict the fresh Milestone 1 build had
  missed: the USB webcam can only be held open by one process at a time.
  `main.py` now releases the OpenCV preview capture before ffmpeg opens the
  camera to record, and reopens it (with a 10-attempt retry loop) once
  ffmpeg exits, matching `legacy/booth.py`.
- Updated `media/ffmpeg.py`'s recording command to match the proven
  audio-sync flags from the prototype: `-thread_queue_size`,
  `-use_wallclock_as_timestamps` on both inputs, `-af
  aresample=async=1:first_pts=0`, and `-avoid_negative_ts make_zero`. Added
  `tests/unit/test_ffmpeg.py` as a regression guard for these flags.
- Updated `media/recorder.py`'s stop sequence to write `q\n` (matching the
  prototype) and fall back to `SIGINT` if the stdin write fails.
- Fixed a window-flag bug in `main.py` (`cv2.namedWindow` was passed
  `cv2.WND_PROP_FULLSCREEN`, a property enum, instead of a window creation
  flag); now uses `cv2.WINDOW_NORMAL` + `cv2.setWindowProperty(...,
  WND_PROP_FULLSCREEN, ...)` as in the prototype.
- Added `cv2.CAP_V4L2` backend hint and `CAP_PROP_BUFFERSIZE=1` to the
  preview capture for lower latency, matching the prototype.

## Unreleased — Fix hardware script microphone check

- Fixed `scripts/test_hardware.sh`: the microphone check grepped for
  `[CARDNAME` in `arecord -l` output, but the short card ID appears right
  after `card N: `, not inside the brackets (that's the longer description).
  The check always failed even when the microphone was present and working.
  Now also supports matching a raw `plughw:N,M` / `hw:N,M` device by card
  index, in addition to the `CARD=NAME` form.

## Unreleased — Configurable A/V sync offset

- Added `av_sync_offset_ms` to booth configuration (default `0`, range
  -5000 to 5000) to correct a constant, device-specific audio/video sync
  offset (PROJECT_SPEC.md section 21, known risk #7) without re-encoding
  video. Positive values delay video via ffmpeg's `-itsoffset`; negative
  values delay audio.
- Documented a calibration procedure (clap test) in the README, and how to
  tell a constant offset apart from progressive drift, which this setting
  does not fix.
- Added unit tests for the new config field and for where `-itsoffset` is
  inserted in the ffmpeg command for positive/negative/zero offsets.

## Unreleased — Software H.264 encode fallback for cameras with no onboard encoder

- Root cause found for "the app won't record" after swapping to a new
  camera (Angetube 4K webcam): `v4l2-ctl --list-formats-ext` confirmed it
  has no H.264 output at all, only MJPG/YUYV. `record_input_format:
  "h264"` with `-c:v copy` fails outright with no H.264 source stream to
  copy.
- `media/ffmpeg.py`'s `build_record_command` now branches on
  `record_input_format`: `"h264"` still copies the camera's stream with no
  re-encode (zero CPU cost, unchanged from before); `"mjpeg"` now
  software-encodes to H.264 via `libx264` (`ultrafast` preset, ~4 Mbps,
  `yuv420p`, constant frame rate) instead of silently producing an
  MJPEG-in-MP4 file. This has not been verified to hold 1280x720@30fps in
  real time on a Pi 4 -- needs on-device testing.
- Changed `config/booth.default.json`'s `record_input_format` to `"mjpeg"`
  to match the currently connected camera.
- Documented how to check camera capability and choose the right
  `record_input_format` in the README ("Camera compatibility" section).
- Added unit tests for both the `copy` and `libx264` encode paths.

## Unreleased — Microphone level tooling and countdown-time gain check

- Added `media/audio_levels.py`: reads raw PCM directly from `arecord` (no
  numpy/sounddevice dependency) and computes RMS/peak dBFS per chunk,
  classified into `MICROPHONE READY` / `SPEAK CLOSER` / `TOO LOUD - MOVE
  SLIGHTLY AWAY` using the targets from PROJECT_SPEC.md section 6.
- Added `media/mixer.py`: best-effort ALSA capture-gain discovery (the
  Mic/Capture control name is resolved at runtime via `amixer scontrols`
  rather than hardcoded, since it differs per USB audio adapter) and a
  one-shot relative gain nudge. Never blocks recording on failure.
- Added `scripts/mic_test.py`: standalone terminal level meter + timestamped
  log file, no camera/video required. Flags clipping and possible dropouts
  in real time. Uses the same level-analysis code as the app, so readings
  match what the app itself sees. Written to help diagnose reported
  feedback/cutting-out issues on the new microphone -- send the generated
  log file for review.
- Wired a countdown-time mic check into `main.py`: samples level in a
  background thread while the countdown runs, shows a live meter + status
  under the countdown number (spec section 8), and applies a single
  ±15% capture-gain nudge right before recording starts based on the
  average level seen -- never continuously during the recording itself
  (spec section 6 explicitly warns against that). The mic-check `arecord`
  process is fully stopped before ffmpeg opens the same ALSA device, same
  handoff pattern as the camera.
- Updated `scripts/test_hardware.sh` to also check `amixer` is installed
  and that a Mic/Capture-like control exists on the configured device.
- Added `tests/unit/test_audio_levels.py` and `tests/unit/test_mixer.py`
  (dBFS math, classification thresholds, mocked `arecord`/`amixer`
  subprocess behavior) -- 31 new tests, none require real hardware.

## Unreleased — Precise mic gain CLI

- Added `scripts/mic_gain.py`: shows/sets the microphone's exact ALSA
  capture percentage via `media/mixer.py`, with an optional `--persist`
  to run `alsactl store`. Written during live gain-tuning on real
  hardware where `alsamixer`'s TUI made it hard to confirm whether a
  change actually took effect between test runs.

## Unreleased — Fix: mixer.py was reading/setting the wrong volume element

- Real-hardware finding: this device's "Mic" ALSA control bundles a
  Playback/monitoring level (shown in `alsamixer`'s `F3` view) and the
  actual Capture level recording uses (shown in `F4`) under one name.
  `amixer sget` prints the Playback line before the Capture line, and
  `media/mixer.py` was matching the *first* `[NN%]` found anywhere in
  that output -- silently reading (and likely also setting, via
  `set_capture_percent`) the wrong element. A screenshot showing Capture
  at `7%` while `mic_test.py` still showed frequent 0 dBFS clipping is
  what surfaced this.
- Fixed `get_capture_percent` (and the new `get_raw_control_info`) to
  anchor specifically to the line containing "Capture" rather than the
  first percentage in the output. Documented in the module docstring so
  it isn't silently reintroduced.
- `set_capture_percent`'s command itself is unchanged (still `amixer sset
  NAME VALUE%` with no direction qualifier) because alsa-utils has no
  reliably version-independent "capture only" flag for `sset` -- this is
  flagged as unverified in its docstring. `mic_gain.py --set` now prints
  the full `amixer sget` output before and after so a human can visually
  confirm the Playback line didn't also move.
- Added regression tests in `test_mixer.py` using a combined
  Playback+Capture fixture with deliberately different percentages, plus
  tests for `get_raw_control_info` and the "no Capture element" case.

## Unreleased — Milestone 2: physical hook-switch integration

- Added `hardware/hook_switch.py`: GPIO17 primary receiver-state signal
  (internal pull-up, active-low) via `gpiozero.Button`, with hardware
  debouncing (~150ms) so brief mechanical bounce during the switch's
  travel is filtered out. GPIO27 is read only for diagnostics
  (`diagnostic_is_active`), matching PROJECT_SPEC.md section 4's caution
  that it "should be treated as diagnostic until the switch is re-tested."
  `gpiozero` is imported lazily and defensively so the module (and the
  rest of the app) still loads/tests fine without it installed.
- Wired into `main.py`: polled once per tick, edge-triggered (lift ->
  start countdown from READY; hang up -> stop and save from RECORDING;
  hang up during COUNTDOWN -> cancel back to READY, per the spec's
  section 20 venue-simulation test case "receiver lifted and immediately
  replaced"). Spacebar keeps working identically and simultaneously
  (architecture rule 11) -- this is not a replacement, both inputs drive
  the same state machine.
- If the GPIO pin can't be claimed at startup (not wired, `gpiozero`/
  backend not installed, pin in use), `open_hook_switch()` catches the
  failure, logs a warning, and the app runs Spacebar-only rather than
  crashing (architecture rule 17).
- Added `hook_switch_enabled` (default `true`) and `hook_switch_gpio_pin`
  (default `17`) to booth configuration, both optional so existing
  configs keep working unchanged.
- Added `gpiozero` and `lgpio` to `requirements.txt`; `install.sh` now
  notes the `python3-lgpio` apt fallback if the pip wheel doesn't build,
  and the `gpio` group requirement.
- `scripts/test_hardware.sh` now checks `gpiozero` is importable and that
  the configured GPIO pin can be claimed (does not simulate an actual
  lift/hang-up -- that still needs a real test on the Pi).
- Added `tests/unit/test_hook_switch.py` (10 tests, using a fake
  `gpiozero.Button` monkeypatched at the module level so these run
  without `gpiozero` installed or real GPIO hardware) and config
  validation tests for the two new fields.

## Unreleased — `recording_mode` setting: eliminate live-encode audio contention

Root cause of the patchy/breaking-up audio on the MJPEG-only camera:
software H.264 encoding during a live recording competes with real-time
audio capture for the Pi's CPU. This adds a real fix (removes the
contention entirely) alongside the existing config-tuning workaround
(lower fps/resolution), as a selectable mode rather than a single
hardcoded behavior.

- Added `recording_mode` config field (`"fast"` | `"quality"`, default
  `"quality"`), only meaningful when `record_input_format` is `"mjpeg"`:
  - `quality`: live capture copies raw MJPEG (`-c:v copy`, near-zero CPU,
    same resource profile a real-H.264 camera has) instead of transcoding
    live. A background job converts it to the final H.264 MP4 after the
    guest hangs up, when there's no real-time deadline to violate.
  - `fast`: today's live-transcode behavior, unchanged. Immediately
    final, but competes with audio capture for CPU. Only worth it if a
    busy event risks the background transcode queue backing up.
- `media/ffmpeg.py`: added `is_live_video_copied()` and
  `needs_deferred_transcode()` as the single source of truth for which
  path applies (shared by the command builder, `recorder.py`, and
  `main.py`), and `build_transcode_command()` for the deferred
  raw-to-final pass (video re-encoded, audio just copied through since
  it was already properly encoded during the live pass).
- `media/recorder.py`: `RecordingSession` now carries `live_output_path`
  (what ffmpeg actually just wrote -- raw `.mkv` or final `.mp4`),
  `final_output_path`, and `needs_transcode`; raw files use a
  `<session-id>.raw.mkv` naming convention (MJPEG doesn't map cleanly
  into MP4). `Recorder.stop()` now returns the full session instead of
  just a path, so callers have everything needed to decide what happens
  next.
- Added `media/transcode.py`: a single-worker background FIFO queue.
  Raw files are never deleted, success or failure, so a message is never
  silently lost -- a failed transcode just leaves the guest's raw
  recording as the only copy, logged clearly with the preserved path.
- `main.py`: starts/stops the transcode queue with the app; after a
  `quality`-mode recording validates successfully, enqueues the
  background job and returns to `SAVED` immediately rather than making
  the guest wait for the transcode.
- Added `tests/unit/test_recorder.py` (raw-vs-final path selection per
  mode, start/stop lifecycle) and `tests/unit/test_transcode.py` (queue
  ordering, success/failure handling, all mocked -- no real ffmpeg or
  threading races), plus `recording_mode` config validation tests and
  ffmpeg command tests for both modes.
