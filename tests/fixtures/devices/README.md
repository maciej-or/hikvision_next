`DS-2TD1228-3-QA.json` contains diagnostic and additional ISAPI GET responses
captured from a DS-2TD1228-3/QA on 2026-09-14. Channel 2 exposes two behavior
rules; channel 1 does not advertise field detection. Serial numbers, addresses,
device/channel/rule names and detection-region coordinates have been replaced
with test values. The fixture contains no Home Assistant configuration or
authentication data. Command tests use mocked HTTP requests.

The per-channel capability and behavior-rule endpoints added to older device
fixtures use HTTP 403 placeholders, as suggested in review of #312. These are
unsupported-endpoint test cases, not newly captured responses from those models.
