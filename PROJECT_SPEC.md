# Raspberry Pi Video Guestbook Phone

## Project Specification and Handoff Document

**Purpose:** Single source of truth for implementing, testing, and maintaining the Raspberry Pi video guestbook phone.

**Target platform:** Raspberry Pi 4 Model B running Raspberry Pi OS 64-bit  
**Current phase:** Hardware integration and Beta software consolidation  
**Primary language:** Python 3  
**Primary media tool:** FFmpeg  
**Display:** 7-inch Waveshare HDMI capacitive touchscreen, 1024 × 600, landscape  
**Beta recording target:** 1280 × 720 at 30 fps with USB microphone audio

---

# 1. Project Goal

Build a self-contained vintage telephone video guestbook that:

1. Displays a continuous live camera preview.
2. Detects when the telephone receiver is lifted.
3. Shows a countdown.
4. Records a video message with audio.
5. Stops and saves when the receiver is returned to the hook.
6. Stores recordings locally in an event-specific directory.
7. Uploads and synchronizes event files with Google Drive when Wi-Fi is available.
8. Supports event-specific themes, graphics, prompts, settings and recording modes.
9. Includes an admin interface for setup, diagnostics, audio levels, themes, recording limits, storage and sync status.
10. Runs automatically at boot without exposing the Raspberry Pi desktop to guests.

The finished unit should behave like an appliance rather than a general-purpose computer.

---

# 2. Physical Concept

The project uses a decorative antique-style telephone enclosure.

## Planned layout

- Brass bells remain at the top.
- Camera is centered near or between the bells.
- A 7-inch landscape touchscreen is mounted in the upper wooden box.
- The vintage receiver remains on the left side.
- The lower wooden box contains:
  - Raspberry Pi 4B
  - display controller board
  - audio hardware
  - USB hub if required
  - storage device
  - wiring and connectors
  - cooling
- The receiver cradle switch controls the guest flow.
- The lower box remains serviceable for maintenance.

## Screen details

Waveshare 7-inch display:

- Native resolution: 1024 × 600
- Orientation: landscape
- Approximate external dimensions: 173 × 103 mm
- Approximate external dimensions: 6.81 × 4.06 inches

The screen should be mounted behind a wooden bezel. Final wood cutting must be based on the physical screen, not listing dimensions alone.

---

# 3. Current Hardware Inventory

## Confirmed hardware

- Raspberry Pi 4 Model B
- Raspberry Pi OS 64-bit
- Keyboard
- Mouse
- USB webcam
- USB microphone
- 7-inch Waveshare HDMI capacitive touchscreen
- Vintage telephone enclosure
- Telephone receiver
- Receiver/cradle switch with three wires
- Speaker or earpiece output confirmed working through the current audio arrangement

## Planned or optional hardware

- External USB SSD, portable hard drive or high-quality USB storage
- USB sound adapter with microphone input, headphone output and Linux support
- Directional electret microphone capsule for the telephone mouthpiece
- Small mono amplifier if the handset earpiece requires amplification
- Small fan or active cooling
- USB hub if power or port count becomes an issue
- Keyed service connector for hook-switch wiring
- Strain relief for display, camera and handset cables

---

# 4. Raspberry Pi Connections

## Display

```text
Raspberry Pi micro-HDMI
        |
        v
Waveshare HDMI controller
        |
        v
7-inch 1024 × 600 screen
```

Touch input:

```text
Waveshare USB touch connection
        |
        v
Raspberry Pi USB
```

## Camera

```text
USB webcam
    |
    v
Raspberry Pi USB
```

The webcam has been detected as:

```text
/dev/video0
/dev/video1
```

The exact device assignment may change after reboot. Production software should identify the camera by stable USB metadata when possible.

## USB microphone

Detected ALSA devices included:

```text
card 3: Device [FDUCE SL40 Audio Device], device 0
card 4: webcamproduct [webcamproduct], device 0
```

The intended microphone is the FDUCE SL40 device.

Preferred stable ALSA identifier:

```text
plughw:CARD=Device,DEV=0
```

Avoid depending permanently on `plughw:3,0`, because ALSA card numbers can change after reboot or when USB devices are rearranged.

## Receiver switch wiring

Current wiring reported:

```text
Black wire -> physical pin 9  -> GND
Red wire   -> physical pin 11 -> GPIO17
Green wire -> physical pin 13 -> GPIO27
```

Important:

- Do not connect the switch to 5V.
- Do not connect the switch to 3.3V.
- Use GPIO internal pull-up resistors.
- Black is currently treated as common ground.

Observed states reported during testing:

```text
Receiver lifted / off-hook:
GPIO17 = 0
GPIO27 = 0

Receiver resting / on-hook:
GPIO17 = 1
GPIO27 = 0
```

Interpretation:

- GPIO17 is currently the useful receiver-state signal.
- GPIO27 appears to stay low and may be permanently connected to ground through the switch.
- Production logic should use GPIO17 first.
- GPIO27 should be treated as diagnostic until the switch is re-tested.
- The app should debounce the switch for approximately 100–200 ms.
- A brief middle or open transition should be ignored.

Recommended initial production behavior:

```text
GPIO17 stable LOW  -> receiver lifted
GPIO17 stable HIGH -> receiver on hook
```

This logic must be verified again after final wiring.

---

# 5. Current Camera Findings

The webcam supports useful MJPEG, H.264 and HEVC modes.

## MJPEG

- 1280 × 720 at 30 fps
- 1920 × 1080 at 30 fps
- 2560 × 1440 at 30 fps

## H.264

- 1280 × 720 at 30 fps
- 1920 × 1080 at 30 fps
- 2560 × 1440 at 30 fps

## Findings

- H.264 preview produced noticeable lag.
- MJPEG preview at 1280 × 720 and 30 fps was much more responsive.
- Changing to the new touchscreen caused preview lag to return.
- Preview performance must be optimized separately from recording quality.

Recommended design:

```text
Live preview:
MJPEG
640 × 360, 1024 × 576 or 1280 × 720
15–30 fps depending on performance

Final recording:
H.264 direct from webcam
1280 × 720 at 30 fps for Beta
Optional 1920 × 1080 at 30 fps after stability testing
```

The preview and recorded video should be treated as separate concerns.

---

# 6. Current Audio Findings

## Confirmed

- USB microphone is detected.
- Audio recording works.
- Live meter testing works.
- Audio was initially quiet.
- ALSA capture gain was increased with `alsamixer`.
- Speaker/earpiece output has been reported working.
- Some clipping is occurring at the current microphone level.
- Audio/video synchronization has required testing and adjustment.

## Audio strategy

Do not depend on aggressive real-time automatic gain control throughout a message. It can cause background music to swell, pumping artifacts and changing tone during speech.

Recommended approach:

1. Set hardware capture gain conservatively so loud speech does not clip.
2. During the countdown, measure ambient and voice level.
3. Warn the guest visually if the microphone level is too low or too high.
4. Lock the capture gain for that message where supported.
5. Record with headroom.
6. Normalize and lightly limit the audio after recording.
7. Preserve the raw recording until post-processing succeeds.

Suggested targets:

```text
Normal speech average: approximately -30 to -12 dBFS
Peaks: below approximately -6 dBFS
Clipping danger: near 0 dBFS
```

## Proposed countdown audio check

During countdown:

- sample microphone input
- show a visual meter
- display one of:
  - MICROPHONE READY
  - SPEAK CLOSER
  - TOO LOUD — MOVE SLIGHTLY AWAY
- avoid continuous gain changes once recording begins unless later testing proves them reliable

## Proposed post-processing

Use FFmpeg filters for:

- loudness normalization
- gentle compression
- limiting
- optional high-pass filtering
- optional light noise reduction

Do not remove the original raw media until the event has been backed up.

---

# 7. Current Software Status

## Confirmed working

- Raspberry Pi OS 64-bit
- USB webcam detection
- Low-lag MJPEG preview
- Full-screen OpenCV preview
- Spacebar-driven state flow
- Three-second countdown
- Camera-only recording
- Automatic conversion to MP4
- 720p H.264 plus USB microphone recording
- FFmpeg recording stop through stdin using the `q` command
- Message save validation
- Multiple recordings during one app session
- Return to READY after saving
- Basic microphone level meter
- Basic hook-switch testing

## Current app location

```text
~/video-booth/app/booth.py
```

## Current recording location

```text
~/video-booth/recordings/
```

## Current log location

```text
~/video-booth/logs/
```

## Current manual launch command

```bash
python3 ~/video-booth/app/booth.py
```

## Current guest controls

```text
Spacebar -> start or stop
Q        -> quit
Escape   -> quit
```

The receiver switch has not yet been fully integrated into the production app.

---

# 8. Current State Machine

```text
READY
  |
  | receiver lifted or Spacebar
  v
COUNTDOWN
  |
  v
RECORDING
  |
  | receiver replaced, Spacebar or time limit reached
  v
SAVING
  |
  +---- success ----> THANK_YOU ----> READY
  |
  +---- failure ----> ERROR --------> READY
```

## READY

- Continuous low-lag live preview
- Event theme overlay
- Prompt: Pick up the receiver to leave a message
- Small status indicators for camera, microphone, storage and sync

## COUNTDOWN

- 3, 2, 1
- microphone level meter
- optional spoken prompt through earpiece
- optional tone
- guest-position guidance

## RECORDING

- red recording indicator
- timer
- microphone meter
- remaining time
- prompt: Hang up to save
- optional on-screen Stop control for admin/testing

## SAVING

- disable guest input
- finalize MP4
- process audio
- create metadata
- queue upload
- verify file integrity

## THANK_YOU

- message saved confirmation
- return to READY after a short delay

## ERROR

- guest-friendly message
- technical details written to logs
- recover without reboot when possible

---

# 9. Recording Modes

## Registry Mode

Purpose: fast guest throughput.

```text
Maximum length: 90 seconds
Recommended default: 90 seconds
Auto-stop: enabled
```

## Party Mode

Purpose: longer stories and messages.

```text
Maximum length: configurable
Suggested default: 180–300 seconds
Auto-stop: enabled
```

Mode names, durations and instructions must be stored in event configuration rather than hard-coded.

---

# 10. Event-Based Project Structure

Every event must be self-contained.

```text
~/video-booth/events/
└── 2026-09-12_smith-jones-wedding/
    ├── event.json
    ├── theme/
    │   ├── theme.json
    │   ├── background.png
    │   ├── frame.png
    │   ├── ready-overlay.png
    │   ├── countdown-overlay.png
    │   ├── recording-overlay.png
    │   ├── saving-overlay.png
    │   ├── thank-you-overlay.png
    │   ├── logo.png
    │   ├── fonts/
    │   └── sounds/
    │       ├── pickup.wav
    │       ├── countdown.wav
    │       ├── start-tone.wav
    │       ├── saved.wav
    │       └── error.wav
    ├── recordings/
    │   ├── raw/
    │   ├── processed/
    │   ├── failed/
    │   └── thumbnails/
    ├── logs/
    ├── metadata/
    ├── exports/
    └── sync/
        ├── pending/
        ├── uploaded/
        └── failed/
```

Example `event.json`:

```json
{
  "event_id": "2026-09-12_smith-jones-wedding",
  "event_name": "Smith & Jones Wedding",
  "event_date": "2026-09-12",
  "mode": "registry",
  "max_recording_seconds": 90,
  "theme": "classic-walnut",
  "record_resolution": "1280x720",
  "record_fps": 30,
  "preview_resolution": "1024x576",
  "preview_fps": 20,
  "audio_device": "plughw:CARD=Device,DEV=0",
  "upload_enabled": true,
  "drive_remote_path": "VideoGuestbook/2026-09-12_smith-jones-wedding"
}
```

---

# 11. Theme System

The UI should load event graphics from the active event directory.

Do not hard-code borders, logos, fonts, colors or text into the recording engine.

A theme may define:

- background image
- decorative border
- logo
- ready screen
- countdown screen
- recording screen
- saving screen
- thank-you screen
- error screen
- button graphics
- fonts
- colors
- sounds
- prompt text
- animation timing

Example `theme.json`:

```json
{
  "name": "Classic Walnut Wedding",
  "font_heading": "fonts/CormorantGaramond-SemiBold.ttf",
  "font_body": "fonts/Montserrat-Medium.ttf",
  "color_primary": "#E8D7B7",
  "color_secondary": "#4A2F22",
  "color_accent": "#B08D57",
  "color_recording": "#C83E3E",
  "overlay_opacity": 0.72,
  "ready_text": "Pick up the receiver to leave a message",
  "recording_text": "Hang up when you are finished",
  "thank_you_text": "Your message has been saved"
}
```

## UI rendering rule

```text
Camera frame
├── clean frame -> final recording
└── display copy -> borders, text, timer, icons and meters
```

By default, decorative UI elements should not be burned into the finished guest video.

---

# 12. Admin Interface

The admin area must be hidden from guests.

Possible access methods:

- long press in a corner
- keyboard shortcut
- PIN
- admin button inside the cabinet
- special hook-switch pattern

## Admin sections

### Dashboard

- active event
- camera status
- microphone status
- speaker status
- hook-switch status
- display status
- free storage
- number of recordings
- upload queue
- Wi-Fi state
- CPU temperature
- app version

### Event setup

- create event
- duplicate event
- select active event
- event name
- event date
- Google Drive destination
- choose mode
- choose theme
- import event package
- export event package

### Recording

- Registry Mode
- Party Mode
- custom maximum length
- countdown duration
- auto-stop
- resolution
- frame rate
- camera selection
- preview quality

### Audio

- microphone selection
- live input meter
- capture gain
- clipping indicator
- speaker level
- test tone
- playback test
- pre-recording level check
- normalization on/off
- limiter on/off
- noise cleanup level

### Theme

- select theme
- preview theme
- reload assets
- change logo
- change prompts
- change fonts
- change sounds
- sync theme from Drive

### Storage

- active storage device
- free space
- recording folder
- safely eject
- fallback to internal storage
- export recordings

### Cloud Sync

- enable/disable
- connect Google Drive
- last sync
- pending files
- failed files
- retry
- upload bandwidth limit
- sync graphics and configuration down
- upload recordings and logs up

### Diagnostics

- test camera
- test microphone
- test speaker
- test hook switch
- test recording
- view logs
- restart media services
- restart app
- safely shut down Pi

---

# 13. Google Drive Synchronization

Recommended tool: `rclone`.

## Required behavior

1. Always record locally first.
2. Never depend on internet access for recording.
3. Add completed files to a local upload queue.
4. Upload only after the file is finalized, validated and metadata is written.
5. Retry failed uploads.
6. Do not block the guest interface during upload.
7. Mark files as uploaded only after verification.
8. Preserve local files until an explicit retention rule removes them.
9. Allow event graphics and settings to sync down from Google Drive.
10. Do not replace active event configuration during a recording.

## Suggested Drive structure

```text
Google Drive
└── VideoGuestbook/
    └── Events/
        └── 2026-09-12_smith-jones-wedding/
            ├── event.json
            ├── theme/
            ├── recordings/
            ├── metadata/
            └── logs/
```

## Sync safety

- Download updates to a staging folder.
- Validate before applying.
- Apply only while the booth is idle.
- Keep the previous theme as a rollback copy.
- Upload recordings asynchronously.
- Use checksums or file-size verification.
- Never delete local recordings because of a Drive-side deletion unless an admin explicitly requests it.

---

# 14. Proposed Repository Structure

```text
video-guestbook/
├── README.md
├── PROJECT_SPEC.md
├── CHANGELOG.md
├── requirements.txt
├── pyproject.toml
├── config/
│   ├── booth.default.json
│   └── logging.yaml
├── src/
│   └── video_guestbook/
│       ├── __init__.py
│       ├── main.py
│       ├── state_machine.py
│       ├── config.py
│       ├── logging_setup.py
│       ├── hardware/
│       │   ├── camera.py
│       │   ├── microphone.py
│       │   ├── speaker.py
│       │   ├── hook_switch.py
│       │   ├── display.py
│       │   └── storage.py
│       ├── media/
│       │   ├── recorder.py
│       │   ├── ffmpeg.py
│       │   ├── audio_processing.py
│       │   ├── validation.py
│       │   └── thumbnails.py
│       ├── ui/
│       │   ├── guest_ui.py
│       │   ├── admin_ui.py
│       │   ├── theme_loader.py
│       │   ├── widgets.py
│       │   └── screens/
│       ├── events/
│       │   ├── event_manager.py
│       │   └── event_schema.py
│       ├── sync/
│       │   ├── rclone_client.py
│       │   ├── upload_queue.py
│       │   └── sync_manager.py
│       └── services/
│           ├── health_monitor.py
│           ├── watchdog.py
│           └── boot_service.py
├── scripts/
│   ├── install.sh
│   ├── setup_audio.sh
│   ├── test_camera.sh
│   ├── test_microphone.sh
│   ├── test_hook_switch.sh
│   └── configure_rclone.sh
├── systemd/
│   ├── video-guestbook.service
│   └── video-guestbook-sync.service
├── tests/
│   ├── unit/
│   ├── integration/
│   └── hardware/
└── sample-events/
    └── demo-wedding/
```

---

# 15. Architecture Rules

1. Hardware access must be isolated behind small modules.
2. The UI must not directly call shell commands.
3. FFmpeg command construction must be centralized.
4. Event configuration must be validated before use.
5. The app must continue recording without Wi-Fi.
6. Missing theme assets must not crash the app.
7. Missing assets should fall back to a default theme.
8. Every recording should have a unique session ID.
9. Raw and processed media must be tracked separately.
10. Errors must be logged with timestamps and session IDs.
11. The physical hook switch and Spacebar should both work during development.
12. Guest input must be ignored during SAVING.
13. A recording is complete only after FFmpeg exits and the file is validated.
14. Cloud sync must run independently of guest recording.
15. Long-running operations must not freeze the UI.
16. Device identifiers must be stable across reboot where possible.
17. The app must recover after camera or microphone disconnects.
18. The app should be testable without physical hardware using simulated devices.

---

# 16. Media Pipeline

## Beta recording pipeline

```text
USB camera H.264
        +
USB microphone PCM
        |
        v
FFmpeg
        |
        v
MP4 with H.264 video and AAC audio
```

Recommended Beta settings:

```text
Resolution: 1280 × 720
Frame rate: 30 fps
Video: copy H.264 from webcam
Audio: AAC
Audio sample rate: 48 kHz
Audio channels: mono
Audio bitrate: 160 kbps
Container: MP4
```

## Post-processing pipeline

```text
Raw MP4
  |
  +--> validate duration, streams and file size
  +--> analyze loudness and peaks
  +--> apply normalization and limiting
  +--> create processed MP4
  +--> generate thumbnail
  +--> write metadata
  +--> queue for upload
```

---

# 17. Preview Performance Plan

The display runs at 1024 × 600. Preview resolution does not need to match recording resolution.

Recommended test order:

1. MJPEG preview at 1024 × 576, 20 fps.
2. If lag persists, test 640 × 360, 20 fps.
3. If needed, use 15 fps.
4. Keep recording at 1280 × 720, 30 fps.
5. Add a configurable preview performance mode.

Admin choices:

```text
Preview quality:
- Low latency
- Balanced
- High quality
```

---

# 18. Startup and Appliance Mode

Production behavior:

1. Raspberry Pi boots.
2. systemd starts the booth app.
3. App loads the active event.
4. App validates display, camera, microphone, storage and hook switch.
5. App opens full-screen.
6. Mouse cursor is hidden.
7. Desktop and taskbar are not visible.
8. App automatically recovers from crashes.
9. Admin can safely shut down from the admin screen.

Suggested systemd policy:

```text
Restart=always
RestartSec=3
```

Do not enable automatic boot until manual testing is stable.

---

# 19. Development Milestones

## Milestone 1 — Consolidate current Beta

- retain current 720p recording
- use stable microphone device name
- preserve Spacebar controls
- move settings into configuration
- retain logs
- confirm 10 consecutive recordings

Acceptance criteria:

- 10 messages save without restarting
- every MP4 plays
- every MP4 contains video and audio
- app returns to READY
- no unrecoverable errors

## Milestone 2 — Hook-switch integration

- lift receiver starts countdown
- hang up stops and saves
- debounce switch
- ignore middle transition
- Spacebar remains backup

Acceptance criteria:

- 20 lift/hang cycles
- no double starts
- no accidental immediate stops
- no corrupted files

## Milestone 3 — Preview optimization

- tune preview for 1024 × 600
- configurable preview resolution and fps
- measure lag
- preserve 720p final recording

## Milestone 4 — Audio calibration and post-processing

- live meter
- clipping warning
- countdown level check
- post-record normalization
- limiter
- preserve raw file

## Milestone 5 — Theme system

- load theme from event directory
- fallback theme
- custom fonts
- PNG overlays
- sounds
- event text

## Milestone 6 — Admin interface

- event setup
- recording modes
- audio controls
- diagnostics
- theme selector
- storage status

## Milestone 7 — Event manager

- create event
- duplicate event
- activate event
- event-specific recordings and assets
- event package import/export

## Milestone 8 — Google Drive sync

- configure rclone
- background upload queue
- retry failures
- download theme/config updates
- sync status in admin

## Milestone 9 — Kiosk and reliability

- systemd startup
- watchdog
- hidden cursor
- automatic recovery
- storage fallback
- safe shutdown

---

# 20. Tests Required Before an Event

## Hardware

- camera cable secure
- screen cable secure
- USB microphone detected
- speaker works
- hook switch stable
- power supply stable
- cooling adequate
- USB storage detected

## Software

- 20 consecutive short recordings
- one maximum-length recording
- low-storage warning
- camera disconnect recovery
- microphone disconnect recovery
- Wi-Fi loss during upload
- restart during pending upload
- reboot and automatic launch
- event selection persistence

## Venue simulation

Test with:

- quiet room
- loud music
- several people speaking
- low light
- bright backlight
- guest standing too close
- guest standing too far away
- receiver lifted and immediately replaced
- receiver left off hook
- repeated rapid switch movement

---

# 21. Known Risks

1. Webcam image quality may be poor in low light.
2. "2K" marketing may not reflect true sensor quality.
3. H.264 camera preview has added lag.
4. The new display configuration has increased preview lag.
5. USB card numbering may change.
6. Microphone gain can clip.
7. Audio sync may need a device-specific offset.
8. Hook-switch wiring is not fully characterized.
9. GPIO27 currently appears permanently low.
10. USB power draw may become excessive with screen, camera, storage and audio.
11. Google Drive sync must never block recording.
12. Theme updates must not corrupt the active event.
13. Final enclosure ventilation must be adequate.

---

# 22. Immediate Next Actions

1. Back up the currently working `booth.py`.
2. Move editable settings into a configuration file.
3. Add stable device discovery for camera and microphone.
4. Integrate GPIO17 receiver logic.
5. Keep Spacebar as backup.
6. Optimize preview for the 1024 × 600 screen.
7. Add microphone meter and clipping warning to the guest UI.
8. Add post-record audio normalization.
9. Run a 10-recording reliability test.
10. Only then begin the event/theme/admin refactor.

---

# 23. Implementation Prompt for Codex or Claude Code

Copy the following prompt into the coding agent after placing this file in the repository:

```text
You are implementing a production-quality Raspberry Pi video guestbook application.

Read PROJECT_SPEC.md completely before modifying any code.

The current working prototype is located at:
~/video-booth/app/booth.py

Your first task is not to rewrite everything. Preserve the working recording behavior and complete Milestone 1 only.

Requirements for Milestone 1:

1. Create a clean Python package structure.
2. Move configuration into a validated JSON or TOML file.
3. Preserve:
   - MJPEG live preview
   - 1280x720 at 30 fps H.264 recording
   - USB microphone recording
   - AAC audio
   - MP4 output
   - FFmpeg stop through stdin using q
   - Spacebar start/stop
   - READY, COUNTDOWN, RECORDING, SAVING, SAVED and ERROR states
4. Use the stable ALSA device:
   plughw:CARD=Device,DEV=0
5. Keep detailed logs.
6. Do not add Google Drive, themes, admin UI or hook-switch behavior yet.
7. Add automated unit tests for:
   - state transitions
   - filename creation
   - configuration validation
   - recording validation logic
8. Add a hardware test script that can confirm:
   - camera exists
   - microphone exists
   - FFmpeg exists
   - output directory is writable
9. Create installation and run instructions.
10. Do not delete or overwrite the original working script. Back it up first.

Before coding:
- summarize the current architecture
- identify risks
- propose the smallest safe implementation plan

After coding:
- show every file changed
- provide exact commands to install and run
- provide exact manual acceptance tests
- do not claim hardware tests passed unless they were actually run on the Raspberry Pi
```

After Milestone 1 is tested on the Pi, use a new coding session for each later milestone.

---

# 24. Decision Log

## Confirmed decisions

- Raspberry Pi 4B is sufficient for the Beta.
- Initial production target is 720p at 30 fps.
- Final recording uses H.264 from the webcam when stable.
- Live preview uses MJPEG for lower latency.
- Screen is landscape at 1024 × 600.
- Current USB microphone is used during Beta.
- Receiver switch will eventually replace Spacebar.
- Spacebar remains an admin/development backup.
- Record locally first.
- Cloud upload is asynchronous.
- Event folders contain recordings, graphics, settings and sync state.
- Themes are file-based, not hard-coded.
- Admin UI controls modes, audio, themes, events, diagnostics and sync.
- Registry Mode defaults to 90 seconds.
- Party Mode allows longer configurable messages.
- Google Drive synchronization should use rclone unless testing identifies a better supported option.
- Finished files remain locally available after upload.

## Decisions still required

- Final camera model
- Final handset microphone capsule
- Final USB audio adapter
- Final earpiece amplifier
- External storage model
- Preview resolution and frame rate
- Exact Party Mode duration
- Whether graphics are ever burned into finished videos
- Whether remote event changes download automatically or only on admin request
- Retention policy for local recordings
- Final admin access method

---

# 25. Safety Notes

- Never wire a mechanical switch to a Pi 5V pin.
- Confirm GPIO header orientation before connecting a three-pin plug.
- Power down before changing GPIO wiring.
- Use strain relief inside the wooden enclosure.
- Avoid exposed conductors.
- Ensure adequate ventilation.
- Use a reliable Pi power supply.
- Do not cut the display opening until the actual screen is measured.
- Keep a local backup of all event recordings.
- Never depend on Google Drive as the only copy.

---

# 26. Definition of Done

The project is complete when:

- it powers on directly into the guest interface
- the preview is responsive
- lifting the receiver begins the workflow
- the microphone level is checked
- the message records with synchronized audio and video
- hanging up stops and saves
- the video is validated and processed
- the guest sees confirmation
- the app returns to READY
- files are stored in the correct event directory
- files upload when internet is available
- the admin interface controls themes, modes, audio, storage and sync
- the unit can operate for an entire event without keyboard or mouse intervention
