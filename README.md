# Hikvision Next

The Home Assistant integration for Hikvision NVRs and IP cameras. Receives and switches detection of alarm events.

## Features
- Real-time Acusense events notifications through binary sensors
- Switches for Acusense events detection
- Holiday mode switch (allows to switch continuous recording with appropriate NVR setup)
- Tracking Alarm Server settings for diagnostic purposes
- Basic and digest authentication support

#### Supported events:
- Motion
- Video Tampering
- Video Loss
- Scene Change
- Intrusion (Field Detection)
- Line Crossing
- Region Entrance
- Region Exiting

#### Preview
![Integration card](/assets/card.jpg "Integration card")
![IP Camera](/assets/ipcam.jpg "IP Camera device view")
![NVR](/assets/nvr.jpg "NVR device view")


The scope supported features depends on device model, setup and firmware version.

## Installation
### With HACS
1. on `HACS / Integrations` page open `Custom repositories` dialog from three dots menu
2. add repository https://github.com/maciej-or/hikvision_next
3. click `Hikvision NVR / IP Camera` card and press `Download` button
4. on `Settings / Devices & Services` page press `+ Add Integration`
5. Search for `Hikvision NVR / IP Camera` and add your Hikvision device using config dialog, repeat the last 2 steps for more devices
### Manual
1. copy `custom_components/hikvision_next` folder into `conifg/custom_components`
2. restart Home Assistant
3. on `Settings / Devices & Services` page press `+ Add Integration`
4. search for `Hikvision NVR / IP Camera` and add your Hikvision device using config dialog, repeat the last 2 steps for more devices

## Hikvision device setup checklist
- Network settings
    - enabled ISAPI access
- User Management - create user with permissions:
    - Remote: Parameters Settings
    - Remote: Log Search / Interrogate Working Status
- Events
    - Notify Surveillance Center
    - Regions if needed
    - Arming Schedule
- Storage Schedule Settings - set continuous recording in Holiday mode for desired cameras
- Alarm Server (optional) - can be set by this integration if checked `Set alarm server` checkbox in the configuration dialog. It will be reverted to `http://0.0.0.0:80/` on integration unload.

## Reporting issues

There are a lot of Hikvision devices with different firmawers in the world. In most cases logs are crucial to solve your problem, so please attach them to the report.
Keep in mind that logs include MAC addresses, serial numbers and local IP addresses of your devices. Consider using [pastebin.com](https://pastebin.com) or similar services for sharing logs.

Setup log level to `debug` in configuration.yaml
```
logger:
  logs:
    custom_components.hikvision_next: debug
```
Restart Home Assistant

Download logs from `Settings / System / Logs`
## Tested models
#### NVR
- 7608NXI-I2/8P/S
#### IP Camera
- DS-2CD2386G2-IU
