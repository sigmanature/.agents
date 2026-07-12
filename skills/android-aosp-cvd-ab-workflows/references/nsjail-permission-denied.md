# AOSP nsjail `MS_PRIVATE` Permission Denied

## Symptom

An Android platform build fails in a Soong/Siso/Ninja action before the wrapped command starts:

```text
initCloneNs():391 mount('/', '/', NULL, MS_REC|MS_PRIVATE, NULL): Permission denied
runChild():485 Launching child process failed
standaloneMode():275 Couldn't launch the child process
```

This commonly appears in generated commands that call:

```text
prebuilts/build-tools/linux-x86/bin/nsjail ...
```

## Diagnosis

First confirm it is a host sandbox problem:

```bash
prebuilts/build-tools/linux-x86/bin/nsjail -q -- /bin/true
echo $?
```

If this exits `255` with the same `mount('/', ..., MS_PRIVATE)` error, the Android target source was not reached. Inspect the host namespace and AppArmor state:

```bash
sysctl kernel.unprivileged_userns_clone user.max_user_namespaces \
  kernel.apparmor_restrict_unprivileged_userns \
  kernel.apparmor_restrict_unprivileged_unconfined 2>/dev/null
cat /proc/self/attr/current 2>/dev/null
findmnt -no TARGET,PROPAGATION /
```

For Soong genrules, note that `SOONG_ACTION_SANDBOXING` only controls global action sandboxing. It does not override a module property such as:

```bp
genrule_defaults {
    use_nsjail: true,
}
```

## Preferred Fix

Fix the host so AOSP's prebuilt `nsjail` can create the namespaces it requires. On Ubuntu systems with AppArmor user namespace restriction enabled, the temporary test command is typically:

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

Then verify:

```bash
prebuilts/build-tools/linux-x86/bin/nsjail -q -- /bin/true
```

If verification passes, rerun the failed target or the original build.

## Fallback

If host policy cannot be changed, a local-only workaround may be to disable the specific `use_nsjail: true` in the owning `Android.bp`, regenerate Soong/Ninja, and rebuild. Treat this as a temporary local workaround because it changes the intended sandboxing contract and may affect reproducibility or hide input declaration issues.
