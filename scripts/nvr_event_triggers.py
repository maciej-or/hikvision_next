#!/usr/bin/env python3
"""List NVR event triggers and toggle 'center' (Notify Surveillance Center) notifications.

Examples:
  python scripts/nvr_event_triggers.py -H http://192.168.28.2 -u viewer -p secret
  python scripts/nvr_event_triggers.py -H http://192.168.28.2 -u viewer -p secret --channel 30
  python scripts/nvr_event_triggers.py -H http://192.168.28.2 -u viewer -p secret --channel 30 --enable
  python scripts/nvr_event_triggers.py -H http://192.168.28.2 -u viewer -p secret --channel 30 --disable --event VMD
  python scripts/nvr_event_triggers.py -H http://192.168.28.2 -u viewer -p secret --event drivingDirectionAtIntersection --enable
  python scripts/nvr_event_triggers.py -H http://192.168.28.2 -u viewer -p secret --channel 43 --event ANPR --exact-event --enable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from base64 import b64encode
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NS = "http://www.isapi.org/ver20/XMLSchema"
CENTER_METHOD = "center"

# User-facing event names -> ISAPI Event/triggers eventType variants (first match wins).
EVENT_QUERY_ALIASES: dict[str, list[str]] = {
    "anpr": ["ANPR", "vehicleDetect", "vehicleDetection"],
    "vmd": ["VMD", "motionDetection", "motiondetection", "vmd"],
    "motiondetection": ["VMD", "motionDetection", "motiondetection", "vmd"],
    "tamperdetection": ["tamperdetection", "Shelteralarm", "shelteralarm"],
    "fielddetection": ["fielddetection", "fieldDetection"],
    "linedetection": ["linedetection", "lineDetection"],
    "regionentrance": ["regionentrance", "regionEntrance"],
    "regionexiting": ["regionexiting", "regionExiting"],
    "scenechangedetection": ["scenechangedetection", "sceneChangeDetection"],
    "facesnap": ["faceSnap", "faceCapture", "facesnap"],
    "facecontrast": ["faceContrast", "facecontrast"],
    "facedetection": ["faceDetection", "facedetection"],
}


@dataclass
class ChannelInfo:
    name: str = "-"
    ip: str = "-"


def ns_tag(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def isapi_request(
    base_url: str,
    path: str,
    username: str,
    password: str,
    *,
    method: str = "GET",
    body: str | None = None,
    timeout: int = 20,
) -> bytes:
    url = f"{base_url.rstrip('/')}/ISAPI/{path.lstrip('/')}"
    headers = {"Accept": "application/xml"}
    data = body.encode("utf-8") if body is not None else None
    if body is not None:
        headers["Content-Type"] = "application/xml"

    token = b64encode(f"{username}:{password}".encode()).decode("ascii")
    req = Request(url, data=data, headers={**headers, "Authorization": f"Basic {token}"}, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as ex:
        detail = ex.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed ({ex.code}): {detail[:500]}") from ex
    except URLError as ex:
        raise RuntimeError(f"{method} {url} failed: {ex}") from ex


def xml_to_dict(elem: ET.Element) -> Any:
    children = list(elem)
    if not children:
        text = (elem.text or "").strip()
        attrs = dict(elem.attrib)
        if attrs and text:
            return {"#text": text, **{f"@{k}": v for k, v in attrs.items()}}
        if attrs:
            return {f"@{k}": v for k, v in attrs.items()}
        return text

    result: dict[str, Any] = {f"@{k}": v for k, v in elem.attrib.items()}
    child_map: dict[str, list[Any]] = {}
    for child in children:
        tag = child.tag.split("}", 1)[-1]
        child_map.setdefault(tag, []).append(xml_to_dict(child))

    for tag, values in child_map.items():
        result[tag] = values[0] if len(values) == 1 else values
    return result


def _elem_local(tag: str) -> ET.Element:
    return ET.Element(tag)


def _append_value_local(parent: ET.Element, tag: str, value: Any) -> None:
    if isinstance(value, dict):
        child = _elem_local(tag)
        for key, item in value.items():
            if key.startswith("@"):
                child.set(key[1:], str(item))
                continue
            _append_value_local(child, key, item)
        parent.append(child)
    elif isinstance(value, list):
        for item in value:
            _append_value_local(parent, tag, item)
    elif value is None:
        return
    else:
        child = _elem_local(tag)
        child.text = str(value)
        parent.append(child)


def dict_to_xml_document(root_tag: str, data: dict[str, Any]) -> str:
    """Serialize trigger dict the way Hikvision ISAPI expects (no namespaced child tags)."""
    root = _elem_local(root_tag)
    root.set("xmlns", str(data.get("@xmlns", NS)))
    for key, value in data.items():
        if key.startswith("@"):
            if key == "@xmlns":
                continue
            root.set(key[1:], str(value))
            continue
        _append_value_local(root, key, value)
    return '<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(root, encoding="unicode")


def parse_triggers(raw: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    triggers: list[dict[str, Any]] = []
    for node in root.iter():
        if node.tag.split("}", 1)[-1] != "EventTrigger":
            continue
        triggers.append(xml_to_dict(node))
    return triggers


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def trigger_channel(trigger: dict[str, Any]) -> int | None:
    for key in ("videoInputChannelID", "dynVideoInputChannelID"):
        if trigger.get(key) not in (None, ""):
            try:
                return int(trigger[key])
            except (TypeError, ValueError):
                pass

    trigger_id = str(trigger.get("id", ""))
    match = re.search(r"-(\d+)$", trigger_id)
    if match:
        return int(match.group(1))
    return None


def notification_entries(trigger: dict[str, Any]) -> list[dict[str, Any]]:
    notif_root = trigger.get("EventTriggerNotificationList")
    if not notif_root:
        return []
    entries = notif_root.get("EventTriggerNotification")
    return [e for e in as_list(entries) if isinstance(e, dict)]


def notification_methods(trigger: dict[str, Any]) -> list[str]:
    methods = []
    for entry in notification_entries(trigger):
        method = entry.get("notificationMethod")
        if method:
            methods.append(str(method))
    return methods


def has_center(trigger: dict[str, Any]) -> bool:
    return CENTER_METHOD in notification_methods(trigger)


def set_center(trigger: dict[str, Any], enabled: bool) -> dict[str, Any]:
    updated = json.loads(json.dumps(trigger))
    entries = notification_entries(updated)

    without_center = [e for e in entries if e.get("notificationMethod") != CENTER_METHOD]
    if enabled:
        new_entries = list(entries)
        if not any(e.get("notificationMethod") == CENTER_METHOD for e in new_entries):
            new_entries.append({"id": "center", "notificationMethod": CENTER_METHOD})
    else:
        new_entries = without_center

    if new_entries:
        value = new_entries[0] if len(new_entries) == 1 else new_entries
        updated["EventTriggerNotificationList"] = {"EventTriggerNotification": value}
    else:
        updated["EventTriggerNotificationList"] = None
    return updated


def parse_channel_event_type_options(raw: bytes) -> list[str]:
    """Return supported eventType values from Event/channels/{id}/capabilities."""
    root = ET.fromstring(raw)
    for node in root.iter():
        if node.tag.split("}", 1)[-1] != "eventType":
            continue
        opt = node.attrib.get("opt") or (node.text or "").strip()
        if opt:
            return [item.strip() for item in opt.split(",") if item.strip()]
    return []


def fetch_channel_capabilities(base_url: str, username: str, password: str, channel: int) -> str:
    try:
        raw = isapi_request(base_url, f"Event/channels/{channel}/capabilities", username, password)
    except RuntimeError:
        return "-"
    options = parse_channel_event_type_options(raw)
    return ",".join(options) if options else "-"


def event_type_candidates(event: str) -> list[str]:
    """Expand a user event filter to possible ISAPI eventType strings."""
    key = event.lower().strip()
    if key == "all":
        return ["all"]
    aliases = EVENT_QUERY_ALIASES.get(key, [])
    candidates = aliases + [event]
    return list(dict.fromkeys(candidates))


def event_type_matches(requested: str, actual: str, *, exact: bool = False) -> bool:
    """Return True when trigger eventType matches the requested filter."""
    actual_l = actual.lower().strip()
    if exact:
        return requested.lower().strip() == actual_l or requested.lower() == "all"
    for candidate in event_type_candidates(requested):
        if candidate.lower() == "all":
            return True
        if candidate.lower() == actual_l:
            return True
    return False


def resolve_event_type_for_channel(
    base_url: str,
    username: str,
    password: str,
    channel: int,
    event: str,
    *,
    exact: bool = False,
) -> str:
    """Pick the ISAPI eventType to create, preferring channel capability matches."""
    if exact:
        event_type = event.strip()
        try:
            raw = isapi_request(base_url, f"Event/channels/{channel}/capabilities", username, password)
            supported = {item.lower() for item in parse_channel_event_type_options(raw)}
            if supported and event_type.lower() not in supported:
                raise RuntimeError(
                    f"Event type {event_type!r} not in channel {channel} capabilities: "
                    f"{', '.join(sorted(supported))}"
                )
        except RuntimeError as ex:
            if "not in channel" in str(ex):
                raise
        return event_type

    candidates = event_type_candidates(event)
    try:
        raw = isapi_request(base_url, f"Event/channels/{channel}/capabilities", username, password)
        supported = {item.lower(): item for item in parse_channel_event_type_options(raw)}
    except RuntimeError:
        supported = {}

    for candidate in candidates:
        if candidate.lower() == "all":
            continue
        if candidate.lower() in supported:
            return supported[candidate.lower()]
    for candidate in candidates:
        if candidate.lower() != "all":
            return candidate
    raise RuntimeError(f"Unable to resolve event type for event={event}")


def channel_binding_field(triggers: list[dict[str, Any]], channel: int) -> str:
    """Infer whether this NVR uses dynVideoInputChannelID or videoInputChannelID."""
    for trigger in triggers:
        if trigger_channel(trigger) != channel:
            continue
        if trigger.get("dynVideoInputChannelID") not in (None, ""):
            return "dynVideoInputChannelID"
        if trigger.get("videoInputChannelID") not in (None, ""):
            return "videoInputChannelID"
    return "dynVideoInputChannelID"


def build_new_trigger(
    *,
    channel: int,
    event_type: str,
    channel_field: str,
    enabled: bool,
) -> dict[str, Any]:
    """Build a minimal EventTrigger document for PUT Event/triggers/{id}."""
    trigger_id = f"{event_type}-{channel}"
    trigger: dict[str, Any] = {
        "id": trigger_id,
        "eventType": event_type,
        channel_field: str(channel),
        "@version": "2.0",
        "@xmlns": NS,
    }
    if enabled:
        trigger["EventTriggerNotificationList"] = {
            "EventTriggerNotification": {
                "id": "center",
                "notificationMethod": CENTER_METHOD,
            }
        }
    else:
        trigger["EventTriggerNotificationList"] = None
    return trigger


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}", 1)[-1]


def _child_text(parent: ET.Element | None, tag: str) -> str | None:
    if parent is None:
        return None
    child = parent.find(ns_tag(tag))
    if child is None or child.text is None:
        return None
    return child.text.strip()


def parse_input_proxy_channels(raw: bytes) -> dict[int, ChannelInfo]:
    channels: dict[int, ChannelInfo] = {}
    root = ET.fromstring(raw)
    for node in root.iter():
        if _local_tag(node) != "InputProxyChannel":
            continue
        channel_id = _child_text(node, "id")
        if not channel_id:
            continue
        name = _child_text(node, "name") or "-"
        source = node.find(ns_tag("sourceInputPortDescriptor"))
        ip = _child_text(source, "ipAddress") or _child_text(source, "hostName") or "-"
        channels[int(channel_id)] = ChannelInfo(name=name, ip=ip)
    return channels


def parse_analog_channels(raw: bytes) -> dict[int, ChannelInfo]:
    channels: dict[int, ChannelInfo] = {}
    root = ET.fromstring(raw)
    for node in root.iter():
        if _local_tag(node) != "VideoInputChannel":
            continue
        channel_id = _child_text(node, "id")
        if not channel_id:
            continue
        name = _child_text(node, "name") or "-"
        channels[int(channel_id)] = ChannelInfo(name=name, ip="-")
    return channels


def fetch_channel_map(base_url: str, username: str, password: str) -> dict[int, ChannelInfo]:
    channels: dict[int, ChannelInfo] = {}
    endpoints = (
        "ContentMgmt/InputProxy/channels",
        "System/Video/inputs/channels",
    )
    parsers = (parse_input_proxy_channels, parse_analog_channels)
    for path, parser in zip(endpoints, parsers, strict=True):
        try:
            raw = isapi_request(base_url, path, username, password)
        except RuntimeError:
            continue
        for channel_id, info in parser(raw).items():
            if channel_id not in channels or channels[channel_id].ip == "-":
                channels[channel_id] = info
    return channels


def parse_http_hosts(raw: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    hosts: list[dict[str, Any]] = []
    for node in root.iter():
        if node.tag.split("}", 1)[-1] != "HttpHostNotification":
            continue
        hosts.append(xml_to_dict(node))
    return hosts


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(cells))

    divider = "-+-".join("-" * w for w in widths)
    lines = [fmt_row(headers), divider]
    lines.extend(fmt_row(row) for row in rows)
    return "\n".join(lines)


def build_rows(
    triggers: list[dict[str, Any]],
    *,
    channel_filter: int | None,
    event_filter: str | None,
    exact_event: bool,
    with_capabilities: bool,
    channel_map: dict[int, ChannelInfo],
    base_url: str,
    username: str,
    password: str,
) -> list[list[str]]:
    rows: list[list[str]] = []
    caps_cache: dict[int, str] = {}

    filtered = []
    for trigger in triggers:
        channel = trigger_channel(trigger)
        if channel_filter is not None and channel != channel_filter:
            continue
        if event_filter and event_filter.lower() != "all":
            if not event_type_matches(event_filter, str(trigger.get("eventType", "")), exact=exact_event):
                continue
        filtered.append((channel or 0, trigger))

    for channel, trigger in sorted(filtered, key=lambda item: (item[0], str(item[1].get("id", "")))):
        methods = notification_methods(trigger)
        center = "yes" if has_center(trigger) else "no"
        caps = "-"
        if with_capabilities and channel:
            if channel not in caps_cache:
                caps_cache[channel] = fetch_channel_capabilities(base_url, username, password, channel)
            caps = caps_cache[channel]

        ch_info = channel_map.get(channel) if channel else None
        row = [
            str(channel or "-"),
            ch_info.name if ch_info else "-",
            ch_info.ip if ch_info else "-",
            str(trigger.get("id", "-")),
            str(trigger.get("eventType", "-")),
            ", ".join(methods) if methods else "-",
            center,
        ]
        if with_capabilities:
            row.append(caps)
        rows.append(row)
    return rows


def trigger_put_candidates(trigger: dict[str, Any]) -> list[str]:
    """Build candidate Event/triggers/{id} paths (bulk list ids are not always PUT ids)."""
    candidates: list[str] = []
    trigger_id = str(trigger.get("id", ""))
    channel = trigger_channel(trigger)
    event_type = str(trigger.get("eventType", ""))

    if channel and event_type:
        candidates.append(f"{event_type}-{channel}")
    if trigger_id:
        candidates.append(trigger_id)
    if channel and trigger_id and not trigger_id.endswith(f"-{channel}"):
        candidates.append(f"{trigger_id}-{channel}")

    return list(dict.fromkeys(c for c in candidates if c))


def fetch_trigger_detail(
    base_url: str,
    username: str,
    password: str,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Load the authoritative per-channel trigger document before PUT."""
    errors: list[str] = []
    for candidate in trigger_put_candidates(trigger):
        try:
            raw = isapi_request(base_url, f"Event/triggers/{candidate}", username, password)
        except RuntimeError as ex:
            errors.append(str(ex))
            continue
        parsed = parse_triggers(raw)
        if parsed:
            return parsed[0]

    channel = trigger_channel(trigger)
    raise RuntimeError(
        f"Cannot fetch trigger detail for event={trigger.get('eventType')} channel={channel}"
        + (f" ({errors[0]})" if errors else "")
    )


def select_modify_targets(
    triggers: list[dict[str, Any]],
    *,
    channel: int | None,
    event: str,
    exact_event: bool,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Return triggers to modify. Error message is set when arguments are insufficient."""
    if channel is None and event.lower() == "all":
        return [], "VMD", "Specify --channel or --event with --enable/--disable"

    modify_event = event if event.lower() != "all" else "VMD"
    targets: list[dict[str, Any]] = []
    for trigger in triggers:
        trigger_channel_id = trigger_channel(trigger)
        if channel is not None and trigger_channel_id != channel:
            continue
        event_type = str(trigger.get("eventType", ""))
        if modify_event.lower() != "all" and not event_type_matches(
            modify_event, event_type, exact=exact_event
        ):
            continue
        targets.append(trigger)
    return targets, modify_event, None


def create_missing_triggers(
    base_url: str,
    username: str,
    password: str,
    triggers: list[dict[str, Any]],
    *,
    channel: int,
    event: str,
    enabled: bool,
    dry_run: bool,
    exact_event: bool,
) -> list[dict[str, Any]]:
    """Create Event/triggers entries that do not exist yet."""
    event_type = resolve_event_type_for_channel(
        base_url, username, password, channel, event, exact=exact_event
    )
    trigger_id = f"{event_type}-{channel}"
    if any(str(trigger.get("id", "")) == trigger_id for trigger in triggers):
        return []
    if any(
        trigger_channel(trigger) == channel
        and str(trigger.get("eventType", "")).lower() == event_type.lower()
        for trigger in triggers
    ):
        return []

    channel_field = channel_binding_field(triggers, channel)
    created = build_new_trigger(
        channel=channel,
        event_type=event_type,
        channel_field=channel_field,
        enabled=enabled,
    )
    trigger_id, xml = build_put_payload(created)
    print(f"  ch {channel} CREATE {trigger_id} eventType={event_type}")
    if dry_run:
        print(xml)
        return [created]

    try:
        isapi_request(base_url, f"Event/triggers/{trigger_id}", username, password, method="PUT", body=xml)
    except RuntimeError as ex:
        hint = ""
        if exact_event and event.lower() == "anpr":
            hint = (
                " This NVR lists ANPR in channel capabilities but rejects it as Event/triggers "
                "(use vehicledetection or vehicleDetection without --exact-event)."
            )
        raise RuntimeError(f"Create Event/triggers/{trigger_id} failed:{hint} {ex}") from ex
    print(f"    PUT Event/triggers/{trigger_id} OK (created)")
    return [created]


def build_put_payload(trigger: dict[str, Any]) -> tuple[str, str]:
    trigger_id = trigger.get("id")
    if not trigger_id:
        raise RuntimeError("Trigger has no id")

    payload = dict(trigger)
    payload.setdefault("@version", "2.0")
    payload.setdefault("@xmlns", NS)
    xml = dict_to_xml_document("EventTrigger", payload)
    return str(trigger_id), xml


def put_trigger(base_url: str, username: str, password: str, trigger: dict[str, Any]) -> str:
    trigger_id, xml = build_put_payload(trigger)
    isapi_request(base_url, f"Event/triggers/{trigger_id}", username, password, method="PUT", body=xml)
    return trigger_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and manage Hikvision NVR Event/triggers center notifications.")
    parser.add_argument("-H", "--host", required=True, help="Device base URL, e.g. http://192.168.28.2")
    parser.add_argument("-u", "--username", required=True, help="ISAPI username")
    parser.add_argument("-p", "--password", required=True, help="ISAPI password")
    parser.add_argument("--channel", type=int, help="Filter by video channel id")
    parser.add_argument(
        "--event",
        default="all",
        help="Filter by event type; with --enable/--disable applies to all matching channels when set",
    )
    parser.add_argument(
        "--exact-event",
        action="store_true",
        help="Use the event name literally (no alias mapping to vehicledetection etc.)",
    )
    parser.add_argument("--enable", action="store_true", help="Add center notification to matching trigger(s)")
    parser.add_argument("--disable", action="store_true", help="Remove center notification from matching trigger(s)")
    parser.add_argument("--with-capabilities", action="store_true", help="Also fetch Event/channels/{id}/capabilities per channel")
    parser.add_argument("--dry-run", action="store_true", help="Show PUT payload without sending")
    args = parser.parse_args()

    if args.enable and args.disable:
        parser.error("Use only one of --enable or --disable")

    raw = isapi_request(args.host, "Event/triggers", args.username, args.password)
    triggers = parse_triggers(raw)

    if args.enable or args.disable:
        targets, modify_event, selection_error = select_modify_targets(
            triggers,
            channel=args.channel,
            event=args.event,
            exact_event=args.exact_event,
        )
        if selection_error:
            parser.error(selection_error)

        if not targets:
            if args.enable and args.channel is not None:
                print(
                    f"No existing triggers for channel {args.channel} event={modify_event}; creating",
                )
                try:
                    created = create_missing_triggers(
                        args.host,
                        args.username,
                        args.password,
                        triggers,
                        channel=args.channel,
                        event=modify_event,
                        enabled=True,
                        dry_run=args.dry_run,
                        exact_event=args.exact_event,
                    )
                except RuntimeError as ex:
                    print(f"Failed to create trigger: {ex}", file=sys.stderr)
                    return 1
                if not created:
                    print(
                        f"No triggers found for channel {args.channel} event={modify_event}",
                        file=sys.stderr,
                    )
                    return 1
                if args.dry_run:
                    return 0
                targets = created
            else:
                scope = f"channel {args.channel} " if args.channel is not None else "all channels "
                hint = " (use --enable with --channel to create missing triggers)" if args.channel else ""
                print(f"No triggers found for {scope}event={modify_event}{hint}", file=sys.stderr)
                return 1

        channel_map = fetch_channel_map(args.host, args.username, args.password)
        action = "enable" if args.enable else "disable"
        print(f"{action} center on {len(targets)} trigger(s) event={modify_event}")

        for trigger in targets:
            channel_id = trigger_channel(trigger)
            ch_info = channel_map.get(channel_id) if channel_id else None
            ch_label = str(channel_id) if channel_id else "-"
            if ch_info:
                ch_label = f"{channel_id} ({ch_info.name}, {ch_info.ip})"

            try:
                detail = fetch_trigger_detail(args.host, args.username, args.password, trigger)
            except RuntimeError as ex:
                print(f"  ch {ch_label} SKIP: {ex}", file=sys.stderr)
                continue

            updated = set_center(detail, enabled=args.enable)
            trigger_id, xml = build_put_payload(updated)
            before = ", ".join(notification_methods(detail)) or "-"
            after = ", ".join(notification_methods(updated)) or "-"
            print(f"  ch {ch_label} {trigger_id}: [{before}] -> [{after}]")
            if args.dry_run:
                print(xml)
                continue
            put_trigger(args.host, args.username, args.password, updated)
            print(f"    PUT Event/triggers/{trigger_id} OK")
        return 0

    headers = ["Channel", "Name", "IP", "Trigger", "EventType", "Notifications", "Center"]
    if args.with_capabilities:
        headers.append("Capabilities")

    event_filter = None if args.event.lower() == "all" else args.event
    channel_map = fetch_channel_map(args.host, args.username, args.password)

    rows = build_rows(
        triggers,
        channel_filter=args.channel,
        event_filter=event_filter,
        exact_event=args.exact_event,
        with_capabilities=args.with_capabilities,
        channel_map=channel_map,
        base_url=args.host,
        username=args.username,
        password=args.password,
    )

    print("=== Event Triggers ===")
    if rows:
        print(format_table(headers, rows))
    else:
        print("(no triggers)")

    try:
        hosts_raw = isapi_request(args.host, "Event/notification/httpHosts", args.username, args.password)
        hosts = parse_http_hosts(hosts_raw)
    except RuntimeError as ex:
        print(f"\n=== HTTP Notification Hosts ===\n(unavailable: {ex})")
        return 0

    host_rows = []
    for host in hosts:
        address = host.get("ipAddress") or host.get("hostName") or "-"
        host_rows.append(
            [
                str(host.get("id", "-")),
                str(host.get("protocolType", "-")),
                str(address),
                str(host.get("portNo", "-")),
                str(host.get("url", "-")),
            ]
        )

    print("\n=== HTTP Notification Hosts (Surveillance Center destination) ===")
    if host_rows:
        print(
            format_table(
                ["ID", "Protocol", "Address", "Port", "URL"],
                host_rows,
            )
        )
    else:
        print("(none configured)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())