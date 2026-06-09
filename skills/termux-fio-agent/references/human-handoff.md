# Human handoff prompts

Use direct handoffs when an action must happen on the phone or inside Termux.

## ADB unauthorized

```text
[HUMAN ACTION REQUIRED]
Please unlock the Android device, approve the USB debugging prompt, and then send me the output of `adb devices` from the host.
```

## Multiple devices

```text
[HUMAN ACTION REQUIRED]
Multiple ADB devices are connected. Please tell me which serial is the target device, or label the devices so I can map each serial to the intended phone.
```

## Termux bootstrap

```text
[HUMAN ACTION REQUIRED]
Please open Termux on the target Android device and run the bootstrap script. When it finishes, send me the lines that start with USER=, HOME=, SSHD=, AUTHORIZED_KEYS=, FIO=, and PYTHON=.
```

## Storage permission

```text
[HUMAN ACTION REQUIRED]
Termux is asking for storage permission. Please tap Allow on the phone, then reply that storage permission is granted.
```

## Root authorization

```text
[HUMAN ACTION REQUIRED]
The device is requesting root permission for Termux. Please unlock the phone and allow Termux in Magisk, KernelSU, APatch, or the root manager. Reply when root is authorized.
```

## SSHD stopped

```text
[HUMAN ACTION REQUIRED]
The SSH connection failed and Termux sshd may not be running. Please open Termux and run `sshd -p 8022`, then reply when it is running.
```

## Need missing registry fields

```text
[HUMAN ACTION REQUIRED]
I need the missing device details before updating the registry. Please send the ADB serial from `adb devices` and the Termux username from `whoami` on that phone.
```
