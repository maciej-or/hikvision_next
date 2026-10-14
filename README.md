# Hikvision Next

![GitHub release (latest by date)](https://img.shields.io/github/v/release/maciej-or/hikvision_next?style=flat-square) [![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)

The Home Assistant integration for Hikvision NVRs, IP cameras and security control panels (SecurityCP). Receives and switches detection of alarm events. Provides video streams.

## Features

- Camera entities for main and sub streams
- Real-time Acusense events notifications through binary sensors and HA events (hikvision_next_event)
- Switches for Acusense events detection
- Switches for NVR Outputs and PIR sensor
- Holiday mode switch (allows to switch continuous recording with appropriate NVR setup)
- Image entities for the latest snapshots
- Tracking HDD and NAS status
- Tracking Notifications Host settings for diagnostic purposes
- Remote reboot device
- Basic and digest authentication support
- Configurable polling interval per device (0 to disable automatic polling entirely)
- **Security control panels (AX Hybrid PRO / AX Hybrid / AX PRO and other `SecurityCP` panels)**: automatically detected on setup, no separate config flow needed
  - Partitions (areas) as `alarm_control_panel` entities (arm away/home, disarm), updated in real time via a persistent push connection
  - Zones as `binary_sensor` entities (door/motion/smoke/gas/moisture/heat/vibration/tamper/safety, mapped from the detector type), each modeled as its own HA device (assignable to its own Area). Armed/alarm/tamper/stay-arming-bypass are its own diagnostic entities (reported for every zone), plus battery/signal/temperature/humidity/via-repeater diagnostics grouped on it for wireless zones only
  - Each zone also gets `switch` entities: Bypass (excludes the zone from the next arming cycle), Chime (doorbell chime on open) and Silent (mute the siren for that zone specifically)
  - Peripherals (keypads, sirens, remotes/keyfobs, repeaters, extension modules) are also modeled as their own HA devices, each with diagnostics for whatever it actually reports (online, tamper, battery/battery low, signal, temperature, mains power) -- e.g. repeaters are only present on fully-wireless AX PRO panels, not the wired-first AX Hybrid PRO, and are simply skipped on panels that don't support them
  - Panel-level diagnostics: AC power, tamper, backup battery level/voltage, active fault count, IP address
  - See [Security control panels](#security-control-panels) below for important notes on real-time updates

### Supported events

- Motion
- Video Tampering
- Video Loss
- Scene Change
- Intrusion (Field Detection)
- Line Crossing
- Region Entrance
- Region Exiting
- NVR Input Triggers
- PIR

**NOTE**
Events must be set to alert the surveillance center in Linkage Action for Home Assistant to be notified. Otherwise related binary sensors and switches will appear as disabled entities.

## Security control panels

Security control panels (SecurityCP: AX Hybrid PRO, AX Hybrid, AX PRO and similar) are auto-detected using the same config flow as NVRs and IP cameras -- just add the panel like any other device.

- **Arm/disarm is real-time**: the integration keeps a persistent connection to the panel (ISAPI "arming with subscription") and reflects arm/disarm changes within about a second.
- **Zone state (door/window/motion/etc.) is polling-based**, not push. This panel family does not deliver a push notification for a zone opening/closing on its own -- only for events it considers alarm-worthy (arm/disarm, tamper, faults). This was confirmed against real hardware and appears to be a limitation of the panel's own CID/ARC event model, not something the integration can influence. Zones update every 30 seconds by default; lower `Polling interval in seconds` in the device options if you want them to feel more responsive (5-10s works well for a panel on a local network).
- Some detector/module combinations have been found to report `magnetOpenStatus` unreliably (stuck at a fixed value regardless of actual door state) -- the integration relies on the zone's own `status` field instead, which was confirmed to track real state correctly.

### Blueprints

#### Take Multiple Snapshots On Detection Event

Creates automation that allows to take snapshots from selected cameras when an event sensor is triggered.

[<img src="https://my.home-assistant.io/badges/blueprint_import.svg">](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/maciej-or/hikvision_next/blob/main/blueprints/take_pictures_on_motion_detection.yaml)

#### Display Sensor State On Hikvision Video

Creates an automation that allows to display text overlay on a selected video stream with the state of a selected sensor. Refreshes every 15 minutes.

[<img src="https://my.home-assistant.io/badges/blueprint_import.svg">](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/maciej-or/hikvision_next/blob/main/blueprints/display_sensor_state_on_hikvision_video.yaml)

## Preview

### IP Camera device view
![IP Camera](/assets/ipcam.jpg "IP Camera device view")

### NVR device view
![NVR](/assets/nvr.jpg "NVR device view")

The scope supported features depends on device model, setup and firmware version.

## Installation

[<img src="https://my.home-assistant.io/badges/hacs_repository.svg">](https://my.home-assistant.io/redirect/hacs_repository/?owner=maciej-or&repository=hikvision_next&category=integration)

### With HACS

1. This integration you will find in the default HACS store. Search for `Hikvision NVR / IP Camera / Alarm Panel` on `HACS / Integrations` page and press `Download` button
2. on `Settings / Devices & Services` page press `+ Add Integration`
3. Search for `Hikvision NVR / IP Camera / Alarm Panel` and add your Hikvision device using config dialog, repeat the last 2 steps for more devices

### Manual

1. copy `custom_components/hikvision_next` folder into `conifg/custom_components`
2. restart Home Assistant
3. on `Settings / Devices & Services` page press `+ Add Integration`
4. search for `Hikvision NVR / IP Camera / Alarm Panel` and add your Hikvision device using config dialog, repeat the last 2 steps for more devices

## Hikvision device setup checklist

- Network settings
  - enabled ISAPI access
- User Management - create user with permissions:
  - Remote: Parameters Settings
  - Remote: Log Search / Interrogate Working Status
  - Remote: Live View
  - Remote: Notify Surveillance Center / Trigger Alarm Output
- Events
  - Notify Surveillance Center
  - Regions if needed
  - Arming Schedule
- Storage Schedule Settings - set continuous recording in Holiday mode for desired cameras
- Notifications Host - IP address of Home Assistant instance for event notifications. Can be set manually or by this integration if checked `Set Notifications Host` checkbox in the configuration dialog. It will be reverted to `http://0.0.0.0:80/` on integration unload.

## Reporting issues

There are a lot of Hikvision devices with different firmwares in the world. In most cases logs are crucial to solve your problem, so please attach them to the report.
Keep in mind that logs include MAC addresses, serial numbers and local IP addresses of your devices. Consider using [pastebin.com](https://pastebin.com) or similar services for sharing logs.

You can also download a `diagnostic output` to provide essential information about your device. It is available on device info page. This redacts all sensitive data and can be provided in the github issue.

Setup log level to `debug` in configuration.yaml

```yaml
logger:
  logs:
    custom_components.hikvision_next: debug
```

Restart Home Assistant

Download logs from `Settings / System / Logs`

## Tested models

### NVR

- Annke N46PCK
- DS-7108NI-Q1/8P
- DS-7608NI-I2
- DS-7608NI-I2/8P
- DS-7608NXI-I2/8P/S
- DS-7608NXI-K1/8P
- DS-7616NI-E2/16P
- DS-7616NI-I2/16P
- DS-7616NI-Q2
- DS-7616NI-Q2/16P
- DS-7616NXI-I2/16P/S
- DS-7716NI-I4/16P
- DS-7732NI-M4
- ERI-K104-P4

### DVR

- iDS-7204HUHI-M1/FA/A
- iDS-7204HUHI-M1/P
- iDS-7208HQHI-M1(A)/S(C)

### IP Camera

- Annke C800 (I91BM)
- DS-2CD2047G2-LU/SL
- DS-2CD2047G2H-LIU
- DS-2CD2087G2-LU
- DS-2CD2146G2-ISU
- DS-2CD2155FWD-I
- DS-2CD2346G2-IU
- DS-2CD2386G2-IU
- DS-2CD2387G2-LU
- DS-2CD2387G2H-LISU/SL
- DS-2CD2425FWD-IW
- DS-2CD2443G0-IW
- DS-2CD2532F-IWS
- DS-2CD2546G2-IS
- DS-2CD2747G2-LZS
- DS-2CD2785G1-IZS
- DS-2CD2H46G2-IZS (C)
- DS-2CD2T46G2-ISU/SL
- DS-2CD2T87G2-L
- DS-2CD2T87G2P-LSU/SL
- DS-2DE4425IW-DE (PTZ)
- DS-2SE4C425MWG-E/26

### Security Control Panel

- DS-PHA48-EP (AX Hybrid PRO)
