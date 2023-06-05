### Major Changes

1. Refactored data structures to:
    a. have a device dataclass (holding NVR/DVR info and single IP cam info)
    b. have an object for each camera holding its relevant info and events and streams (particularly as these can be different for different cameras on same NVR)
    c. have object classes for envents and steams attached to camera object
    d. used get instead of bracketed syntax to get values as handles it not existing without error.
    e. build this data structure when calling get_hw_info

2. Used different method to establish if NVR/DVR or single camera
    a. /ISAPI/System/capabilities lists VideoCap and InputProxy supported channels.  For a NVR/DVR this will always add up to more than 1.  For a single IP camera this will be 0 as InputProxy is not listed and VideoCap -> videoInputPortNums will be 0.

3. Support for multiple installs by making notification a class and only loading if first instance.  Prevented multiple events being fired.

### Minor Changes

1. Used unique id in config flow instead of looking for device - seemed an issue when removing and re-adding if Onvif installed
2. get_device_info ensures correct return for new data structure
3. Added HA event to fire on each nortification
4. Added check to enable improved error if event types are mutually exclusive.  Ie motion deteciton cannot be enabled if line detection is enabled on any channel.  Got this from web admin pages javascript.
5. Added ability to delete devices via UI
6. Added return xml html encode check as some camera return invalid xml

### Known Issues and To Do's

1. When installing, only shows single device.  If refresh page after install, shows all.  Need to look at building camera objects more efficiently.
2. Found a better way to establish supported events from web admin page javascript.  Need to update code to use this instead.  Shows videoloss and tampering support instead of guessing.
3. Add diagnostics download option from UI
4. Code clean up - maybe some code/constants not now used that need cleaning up.
5. Update documentation
