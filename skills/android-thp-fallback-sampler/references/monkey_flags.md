# Monkey flags quick reference

## Minimal form

```bash
adb shell monkey -p <package> <events>
```

- `-p <package>`: constrain events to one app (recommended)
- `<events>`: number of pseudo-random events to send

If you omit `-p`, Monkey can wander across **all** apps on the device.

## Reproducibility

```bash
adb shell monkey -p <package> -s <seed> <events>
```

- `-s <seed>` fixes the random seed so the run is repeatable.

## Pace control

```bash
adb shell monkey -p <package> --throttle 75 <events>
```

- `--throttle <ms>` adds delay between events (reduces flakiness, improves log readability).

## Safer defaults for app stability testing
Common “don’t brick my session” flags:

```bash
--pct-syskeys 0 --pct-majornav 0 \
--ignore-crashes --ignore-timeouts \
--monitor-native-crashes --kill-process-after-error \
-v -v
```

Notes:
- `--pct-syskeys 0` reduces HOME/POWER/VOL/etc.
- `--pct-majornav 0` reduces BACK/MENU navigation that can exit the app.
- `--monitor-native-crashes` helps catch native crashes.
- `--kill-process-after-error` restarts app after error so the run can continue.

## Event mix tuning (optional)

```bash
--pct-touch 70 --pct-motion 20 --pct-appswitch 10
```

Typical knobs:
- `--pct-touch`: taps/clicks
- `--pct-motion`: swipes/drag
- `--pct-appswitch`: app switching (often set low if you want to stay inside app)

## Activity constraint (optional)
If you want to start from a specific activity, launch it first (recommended) then run monkey:

```bash
adb shell am start -n <package>/<activity>
adb shell monkey -p <package> <events>
```

## Fast “does the package exist?” check

```bash
adb shell pm path <package>
```
