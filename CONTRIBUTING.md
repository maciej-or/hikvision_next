## Linting

- Install Ruff extension and select ruff as default formatter in vscode settings
- Code must follow [Home Assistant code style guidelines](https://developers.home-assistant.io/docs/development_guidelines)
- Line length limit must be 120 characters

## Testing

There are 2 types of tests:

### ISAPI communication
They test specific ISAPI requests and uses data from `fixtures/ISAPI`. They are focued on data processing. The folder structure corresponds to ISAPI endpoints, and the XML files contain device responses.

### The behavior of the device in HomeAssistant

They initialize the entire device in the HomeAssistant environment and uses data from `fixtures/devices`. Each JSON file contains all the responses to GET requests sent by the given device.

They can be recorded for any device in the Device Info window by clicking DOWNLOAD DIAGNOSTICS button. All sensitive data such as MAC addresses, IPs, and serial numbers are anonymized so they can be safely made public.

This approach should make it easier to develop this integration for an even greater number of devices without the need for physical access to the device.
