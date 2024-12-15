# Contributing to hikvision_next
Everybody is invited and welcome to contribute to hikvision_next integration.

-   Pull requests are always created against the [**dev**](https://github.com/maciej-or/hikvision_next/tree/dev) branch.
- Code must follow [Home Assistant code style guidelines](https://developers.home-assistant.io/docs/development_guidelines)
- Line length limit must be 120 characters
- Install Ruff extension and select ruff as default formatter in vscode settings

## Development Environment
1. Set up devcontainer using guide at https://developers.home-assistant.io/docs/development_environment/
2. Mount the integration repo in `.devcontainer/devcontainer.json`
```
  "mounts": [
    "source=/path/on/your/disk/hikvision_next/custom_components/hikvision_next,
    target=${containerWorkspaceFolder}/config/custom_components/hikvision_next,
    type=bind"
  ]
```
3. Run / Start Debugging

NOTE: the first container build and launch Home Assistant may take longer.

## Test Environment

Install python 3.12 or later and project dependencies:
```
pip install -r requirements.test.txt
```
and run `pytest`. For running tests in debug mode vscode is recommended.

There are 2 types of tests:

### ISAPI communication
They test specific ISAPI requests and use data from `tests/fixtures/ISAPI`. They are focued on data processing. The folder structure corresponds to ISAPI endpoints, and the XML files contain device responses.

### The behavior of the Hikvision device in HomeAssistant

They initialize the entire device in the HomeAssistant environment and use data from `tests/fixtures/devices`. Each JSON file contains all the responses to GET requests sent by the given device.

The fixtures can be recorded for any device in the Device Info window by clicking DOWNLOAD DIAGNOSTICS button. All sensitive data such as MAC addresses, IPs, and serial numbers are anonymized so they can be safely made public.

This approach should make it easier to develop this integration for an even greater number of devices without the need for physical access to the device.

### NOTE: Test Environment on Windows

If you develop on windows, there are some limits with pytest-homeassistant-custom-component when you want to run the unit tests

See this [issue](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component/issues/154) for more detail.

#### RUN Test Environment on Windows
If you just want run the tests, the simplest way is to use docker on your PC:

1. Install [docker](https://docs.docker.com/desktop/setup/install/windows-install/)

2. in a cmd window run:
```
REM go to your hikvision_next directory. example:
cd C:\Users\john\Documents\github\hikvision_next

REM Build a docker container
docker build -f run_test.dockerfile -t mine/pytest-homeassistant-custom-component:latest .

REM Clear console and Run test in your container
cls && docker run --rm -v .:/app mine/pytest-homeassistant-custom-component:latest
```

#### DEBUG Test Environment in VS Code on windows
If you want to debug the tests in Visual Studio Code:

1. Install [docker](https://docs.docker.com/desktop/setup/install/windows-install/)

2. Configure VS Code tasks(in ./vscode/task.json)
```
{
    "tasks": [
        {
          "label": "docker-build phcc",
          "type": "docker-build",
          "platform": "python",
          "dockerBuild":{
            "context": "${workspaceFolder}",
            "dockerfile":"${workspaceFolder}/run_test.dockerfile",
            "tag":"minevs/pytest-homeassistant-custom-component:latest"
          }
        },
        {
            "label": "docker-run: debug",
            "type": "docker-run",
            "dependsOn": ["docker-build phcc"],
            "python": {
              "args": ["."],
              "module": "pytest"
            },
            "dockerRun":{
                "image":"minevs/pytest-homeassistant-custom-component:latest",
                "volumes":[{
                    "localPath":"${workspaceFolder}",
                    "containerPath": "/app"
                }]
            }
        }
    ]
}
```

3. Configure VS Code launcher(in ./vscode/launch.json)
```
{
    "version": "0.2.0",
    "configurations": [
        {
          "name": "Docker: Python tests debug",
          "type": "docker",
          "request": "launch",
          "preLaunchTask": "docker-run: debug",
          "python": {
            "pathMappings": [
              {
                "localRoot": "${workspaceFolder}",
                "remoteRoot": "/app"
              }
            ]
          }
        }
    ]
}
```

4. add a breakpoint

5. run the "Docker: Python tests debug" job in VS Code
