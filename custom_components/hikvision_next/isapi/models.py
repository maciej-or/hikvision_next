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
