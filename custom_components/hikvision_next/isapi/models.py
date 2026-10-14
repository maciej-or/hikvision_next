from dataclasses import dataclass, field


@dataclass
class AlarmServer:
    """Holds alarm server info."""

    ip_address: str
    host_name: str
    port_no: int
    url: str
    protocol_type: str


@dataclass
class AlertInfo:
    """Holds NVR/Camera event notification info."""

    channel_id: int
    io_port_id: int
    event_id: str
    device_serial_no: str = field(default=None)
    mac: str = ""
    region_id: int = 0
    detection_target: str = field(default=None)
    target_type: str = field(default=None)


@dataclass
class MutexIssue:
    """Holds mutually exclusive event checking info."""

    event_id: str
    channels: list = field(default_factory=list)


@dataclass
class EventInfo:
    """Holds event info of Hikvision device."""

    id: str
    channel_id: int
    io_port_id: int
    unique_id: str = None
    url: str = None  # URL to fetch the event status (enabled/disabled)
    is_proxy: bool = False  # True if the event comes from device connected via NVR
    disabled: bool = False
    notifications: list[str] = field(default_factory=list)


@dataclass
class CameraStreamInfo:
    """Holds info of a camera stream."""

    id: int
    name: str
    type_id: int
    type: str
    enabled: bool
    codec: str
    width: int
    height: int
    audio: bool
    use_alternate_picture_url: bool = False


@dataclass
class StorageInfo:
    """Holds info for internal and NAS storage devices."""

    id: int
    name: str
    type: str
    status: str
    capacity: int
    freespace: int
    property: str
    ip: str = ""


@dataclass
class ISAPIDeviceInfo:
    """Holds info of an NVR/DVR or single IP Camera."""

    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial_no: str = ""
    firmware: str = ""
    mac_address: str = ""
    ip_address: str = ""
    device_type: str = ""
    is_nvr: bool = False
    is_security_panel: bool = False  # True for AX Hybrid/Hybrid PRO/PRO intrusion alarm panels


@dataclass
class CapabilitiesInfo:
    """Holds info of an NVR/DVR or single IP Camera."""

    analog_cameras_inputs: int = 0  # number of analog cameras connected to NVR/DVR
    digital_cameras_inputs: int = 0  # number of digital cameras connected to NVR/DVR
    is_multi_channel: bool = False  # if camera has multiple channels
    support_holiday_mode: bool = False
    support_alarm_server: bool = False
    support_channel_zero: bool = False
    support_event_mutex_checking: bool = False
    input_ports: int = 0
    output_ports: int = 0
    partitions: int = 0  # number of partitions (areas) on a security control panel
    zones: int = 0  # number of zones on a security control panel


@dataclass
class Partition:
    """Holds info of a security control panel partition (area)."""

    id: int
    name: str
    unique_id: str = None
    enabled: bool = True
    arming: str = "disarm"  # "stay", "away", "disarm", "arming"
    alarm: bool = False
    delay_time: int = 0


@dataclass
class Zone:
    """Holds info of a security control panel zone."""

    id: int
    name: str
    unique_id: str = None
    partition_id: int = 0
    detector_type: str = "other"
    zone_type: str = "Instant"
    status: str = "notRelated"  # online/offline/trigger/breakDown/heartbeatAbnormal
    alarm: bool = False
    bypassed: bool = False
    tamper_evident: bool = False
    armed: bool = False
    charge: str = "normal"  # "normal"/"lowPower"
    # Real-time open/closed state, only reported for magnetic contact (door/window)
    # detectors. Confirmed against real hardware to be unreliable on some wireless
    # zones (stuck at a fixed value regardless of actual open/close) -- kept only as an
    # informational attribute; "status" == "trigger" is what actually drives the
    # binary_sensor now (see coordinator._zone_is_triggered).
    magnet_open_status: bool | None = None
    # Telemetry only reported by wireless zones (battery-powered, RF-connected); wired
    # zones don't return these fields at all.
    charge_value: int | None = None  # remaining battery percentage, 0-100
    signal: int | None = None  # RF signal quality/strength
    temperature: int | None = None  # detector-reported temperature
    humidity: int | None = None  # humidity detector reading, 10-90%
    # "wired"/"wireless" -- the authoritative signal for whether this zone is a physical
    # wired input on the panel/an expansion module (no battery/signal/repeater at all) vs an
    # RF-paired wireless detector. Confirmed against real hardware: a purely mechanical wired
    # zone (e.g. a hardwired panel input) never reports this field at all.
    zone_attrib: str | None = None
    is_via_repeater: bool | None = None  # wireless only: whether signal is relayed via a repeater
    stay_away: bool | None = None  # whether stay-arming bypass is enabled for the zone
    model: str | None = None  # detector model, e.g. "DS-PDMC-EG2"
    version: str | None = None  # detector firmware version
    # Detector's own serial number ("detectorSeq") -- only reported by the Configuration
    # endpoint (not status), and only for wireless zones with a paired physical detector.
    serial_no: str | None = None
    chime_enabled: bool | None = None  # whether a doorbell chime sounds when the zone opens
    silent_enabled: bool | None = None  # whether the siren is muted for this zone specifically


@dataclass
class Peripheral:
    """Holds info of a security control panel peripheral (keypad, siren, remote/keyfob,
    repeater or extension module) -- anything that isn't a zone or partition but is a
    distinct physical unit paired to the panel, reported by its own SecurityCP/status/*
    endpoint. Not every field applies to every kind (e.g. remotes/keyfobs don't report
    status/tamper/signal/temperature) -- absent fields stay None.
    """

    kind: str  # "keypad", "siren", "remote", "repeater", "extension_module"
    id: int
    name: str
    unique_id: str = None
    serial_no: str | None = None  # peripheral's own serial number ("seq")
    model: str | None = None
    version: str | None = None
    status: str | None = None  # online/offline/notRelated/heartbeatAbnormal
    tamper_evident: bool | None = None
    charge: str | None = None  # "normal"/"lowPower"
    charge_value: int | None = None  # remaining battery percentage, 0-100
    signal: int | None = None  # RF signal quality/strength, 0-255
    temperature: int | None = None
    mains_power: bool | None = None  # external/AC power connected (wireless units)


@dataclass
class SecurityHostStatus:
    """Holds security control panel host-level status (not specific to any zone/partition)."""

    tamper_evident: bool = False  # panel cover/case open
    ac_connected: bool = True  # mains (220V) power present
    fault_count: int = 0  # total number of active faults reported by the panel
    battery_status: str = "normal"  # "normal"/"lowPower"/"miss" (backup battery missing)
    battery_percent: int | None = None
    battery_voltage: float | None = None


@dataclass
class AnalogCamera:
    """Analog cameras info."""

    id: int
    name: str
    model: str
    serial_no: str
    input_port: int
    connection_type: str
    streams: list[CameraStreamInfo] = field(default_factory=list)
    events_info: list[EventInfo] = field(default_factory=list)


@dataclass
class IPCamera(AnalogCamera):
    """IP/Digital camera info."""

    firmware: str = ""
    ip_addr: str = ""
    ip_port: int = 0


@dataclass
class ProtocolsInfo:
    """Holds info of supported protocols."""

    rtsp_port: int = 554
