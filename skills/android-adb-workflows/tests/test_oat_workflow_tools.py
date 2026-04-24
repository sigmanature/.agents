import json
import os
import struct
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_DIR = Path.home() / ".agents" / "skills" / "android-adb-workflows"
SCRIPTS_DIR = SKILL_DIR / "scripts"
TIMELINE_MERGE = SCRIPTS_DIR / "android_timeline_merge.py"
PM_ART_DUMP_SUMMARY = SCRIPTS_DIR / "pm_art_dump_summary.py"
VDEXDUMP_MIN = SCRIPTS_DIR / "vdexdump_min.py"
OAT_ARTIFACT_MANIFEST = SCRIPTS_DIR / "oat_artifact_manifest.py"
CNFE_CLASS_EXTRACT = SCRIPTS_DIR / "extract_cnfe_classes.py"
CAPTURE_SCRIPT = SCRIPTS_DIR / "adb_oat_rewrite_capture.sh"
REGEN_SCRIPT = SCRIPTS_DIR / "adb_dexopt_regen_loop.sh"
FREEZE_SCRIPT = SCRIPTS_DIR / "adb_oat_invariant_freeze.sh"
OATDUMP_SCRIPT = SCRIPTS_DIR / "run_device_oatdump.sh"


class OatWorkflowToolTests(unittest.TestCase):
    maxDiff = None

    def run_py(self, script: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(script), *args],
            text=True,
            capture_output=True,
            check=check,
        )

    def test_timeline_merge_emits_raw_json_and_html_without_losing_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            logcat = tmp / "logcat.txt"
            dmesg = tmp / "dmesg.txt"
            trace = tmp / "trace.json"
            raw_json = tmp / "merged.json"
            html = tmp / "merged.html"

            logcat.write_text(
                "\n".join(
                    [
                        "04-21 12:00:00.100  1111  1111 I ActivityTaskManager: Displayed com.ss.android.ugc.live/.SplashActivity for user 0",
                        "04-21 12:00:00.200  1111  1111 I ActivityTaskManager: LaunchState COLD </script> marker",
                        "04-21 12:00:00.300  1111  1111 E AndroidRuntime: Process: com.ss.android.ugc.live, PID: 1111",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            dmesg.write_text(
                "[Tue Apr 21 12:00:00 2026] pid=1111 ino=19225 comm=live path=/data/app/pkg/oat/arm64/base.vdex f2fs_wait_on_page_writeback\n",
                encoding="utf-8",
            )
            trace.write_text(
                " \n"
                + json.dumps(
                    {
                        "events": [
                            {
                                "line_no": 1,
                                "task": "live",
                                "tid": 1111,
                                "cpu": 0,
                                "timestamp": "1.000000",
                                "phase": "enter",
                                "nr": 56,
                                "syscall": "openat",
                                "fields": [
                                    {"name": "dirfd", "raw": "-100", "value": -100, "display": "AT_FDCWD"},
                                    {
                                        "name": "pathname",
                                        "raw": '"/data/app/pkg/oat/arm64/base.vdex"',
                                        "value": "/data/app/pkg/oat/arm64/base.vdex",
                                        "display": "/data/app/pkg/oat/arm64/base.vdex",
                                    },
                                ],
                                "raw_args": [],
                                "raw_line": "trace line",
                                "return_display": None,
                                "annotations": [],
                                "path_hints": {"pathname": "/data/app/pkg/oat/arm64/base.vdex"},
                            }
                        ],
                        "skipped_lines": 3,
                        "syscall_table_size": 400,
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_py(
                TIMELINE_MERGE,
                "--logcat",
                str(logcat),
                "--dmesg",
                str(dmesg),
                "--trace",
                str(trace),
                "--trace-anchor-monotonic",
                "1.0",
                "--trace-anchor-wall",
                "2026-04-21T12:00:00",
                "--year",
                "2026",
                "--window-start",
                "2026-04-21T12:00:00",
                "--window-end",
                "2026-04-21T12:00:01",
                "--max-events-per-cell",
                "1",
                "--raw-json-out",
                str(raw_json),
                "--html-out",
                str(html),
                "--no-stdout-table",
            )

            self.assertEqual(result.stdout.strip(), "")
            payload = json.loads(raw_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["meta"]["warnings"]["trace_skipped_lines"], 3)
            self.assertEqual(len(payload["rows"]), 1)
            self.assertEqual(
                payload["rows"][0]["summary"]["headline"],
                "3 logcat, 1 dmesg, 1 syscall event(s)",
            )
            self.assertTrue(payload["rows"][0]["summary"]["highlights"])
            self.assertEqual(len(payload["rows"][0]["cells"]["logcat"]), 3)
            self.assertEqual(
                payload["rows"][0]["cells"]["logcat"][1]["raw_line"],
                "04-21 12:00:00.200  1111  1111 I ActivityTaskManager: LaunchState COLD </script> marker",
            )
            self.assertEqual(
                payload["rows"][0]["cells"]["syscall"][0]["source_raw"]["raw_line"],
                "trace line",
            )
            html_text = html.read_text(encoding="utf-8")
            self.assertIn("Process: com.ss.android.ugc.live, PID: 1111", html_text)
            self.assertIn("\\u003c/script\\u003e marker", html_text)
            self.assertNotIn("LaunchState COLD </script> marker", html_text)
            self.assertIn("row-summary", html_text)
            self.assertIn("summary.highlights", html_text)

    def test_timeline_merge_path_filter_keeps_logcat_text_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            logcat = tmp / "logcat.txt"
            dmesg = tmp / "dmesg.txt"
            raw_json = tmp / "merged.json"

            logcat.write_text(
                "\n".join(
                    [
                        (
                            "11-11 17:22:13.201  7016  7017 I artd    : "
                            "Opened FDs: 8:/data/app/pkg/oat/arm64/base.vdex.tmp"
                        ),
                        "11-11 17:22:13.604  7029  7084 I ndroid.ugc.live: unrelated startup line",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            dmesg.write_text(
                "[Tue Nov 11 17:22:13 2025] pid=7029 ino=19225 comm=live unrelated kernel line\n",
                encoding="utf-8",
            )

            self.run_py(
                TIMELINE_MERGE,
                "--logcat",
                str(logcat),
                "--dmesg",
                str(dmesg),
                "--year",
                "2025",
                "--window-start",
                "2025-11-11T17:22:13",
                "--window-end",
                "2025-11-11T17:22:14",
                "--path-substr",
                "base.vdex",
                "--raw-json-out",
                str(raw_json),
                "--no-stdout-table",
            )

            payload = json.loads(raw_json.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["rows"]), 1)
            self.assertEqual(len(payload["rows"][0]["cells"]["logcat"]), 1)
            self.assertIn(
                "Opened FDs: 8:/data/app/pkg/oat/arm64/base.vdex.tmp",
                payload["rows"][0]["cells"]["logcat"][0]["raw_line"],
            )
            self.assertEqual(payload["rows"][0]["cells"]["dmesg"], [])

    def test_timeline_merge_decoded_futex_is_rendered_compactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace = tmp / "trace.json"
            raw_json = tmp / "merged.json"

            trace.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "line_no": 1,
                                "task": "live",
                                "tid": 1111,
                                "cpu": 0,
                                "timestamp": "1.000000",
                                "phase": "enter",
                                "nr": 98,
                                "syscall": "futex",
                                "fields": [
                                    {"name": "arg0", "raw": "1", "value": 1, "display": "0x1"},
                                    {"name": "arg1", "raw": "80", "value": -128, "display": "0xffffffffffffff80"},
                                    {"name": "arg2", "raw": "7fffffff", "value": 2147483647, "display": "0x7fffffff"},
                                ],
                                "raw_args": ["1", "80", "7fffffff"],
                                "raw_line": "trace enter",
                                "return_display": None,
                                "annotations": [],
                                "path_hints": {},
                            },
                            {
                                "line_no": 2,
                                "task": "live",
                                "tid": 1111,
                                "cpu": 0,
                                "timestamp": "1.000100",
                                "phase": "exit",
                                "nr": 98,
                                "syscall": "futex",
                                "fields": [
                                    {"name": "arg0", "raw": "1", "value": 1, "display": "0x1"},
                                    {"name": "arg1", "raw": "80", "value": -128, "display": "0xffffffffffffff80"},
                                    {"name": "arg2", "raw": "7fffffff", "value": 2147483647, "display": "0x7fffffff"},
                                ],
                                "raw_args": ["1", "80", "7fffffff"],
                                "raw_line": "trace exit",
                                "return_display": "0",
                                "annotations": [],
                                "path_hints": {},
                            },
                        ],
                        "skipped_lines": 0,
                        "syscall_table_size": 400,
                    }
                ),
                encoding="utf-8",
            )

            self.run_py(
                TIMELINE_MERGE,
                "--trace",
                str(trace),
                "--trace-anchor-monotonic",
                "1.0",
                "--trace-anchor-wall",
                "2026-04-21T12:00:00",
                "--window-start",
                "2026-04-21T12:00:00",
                "--window-end",
                "2026-04-21T12:00:01",
                "--raw-json-out",
                str(raw_json),
                "--no-stdout-table",
            )

            payload = json.loads(raw_json.read_text(encoding="utf-8"))
            syscall_events = payload["rows"][0]["cells"]["syscall"]
            self.assertEqual(syscall_events[0]["text"], "live-1111 enter futex")
            self.assertEqual(syscall_events[1]["text"], "live-1111 exit futex ret=0")

    def test_pm_art_dump_summary_extracts_effective_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dump = Path(tmpdir) / "art_dump.txt"
            dump.write_text(
                textwrap.dedent(
                    """\
                    Package [com.ss.android.ugc.live]
                      arm64: [status=speed-profile] [reason=cmdline] [primary-abi]
                        [location is /data/app/pkg/oat/arm64/base.odex]
                      arm: [status=verify] [reason=bg-dexopt]
                    """
                ),
                encoding="utf-8",
            )
            result = self.run_py(PM_ART_DUMP_SUMMARY, str(dump))
            payload = json.loads(result.stdout)
            self.assertEqual(payload["entries"][0]["abi"], "arm64")
            self.assertEqual(payload["entries"][0]["status"], "speed-profile")
            self.assertEqual(payload["entries"][0]["reason"], "cmdline")
            self.assertEqual(payload["entries"][0]["location"], "/data/app/pkg/oat/arm64/base.odex")
            self.assertEqual(payload["entries"][1]["status"], "verify")

    def test_extract_cnfe_classes_normalizes_androidruntime_and_am_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logcat = Path(tmpdir) / "logcat.txt"
            logcat.write_text(
                textwrap.dedent(
                    """\
                    04-21 12:00:00.100  1111  1111 E AndroidRuntime: Caused by: java.lang.NoClassDefFoundError: Failed resolution of: Lcom/bytedance/android/live/base/model/message/HotWord;
                    04-21 12:00:00.101  1111  1111 E AndroidRuntime: Caused by: java.lang.ClassNotFoundException: com.bytedance.android.live.base.model.message.HotWord
                    04-21 12:00:00.102  1550  2365 I am_crash: [17891,0,com.ss.android.ugc.live,951598660,java.lang.ClassNotFoundException,com.bytedance.android.live.base.model.message.HotWord,SourceFile,195,0]
                    04-21 12:00:00.103  1111  1111 E AndroidRuntime: Didn't find class \"com.bytedance.android.live.base.model.message.EntertainmentPaidData\" on path: DexPathList[]
                    """
                ),
                encoding="utf-8",
            )
            result = self.run_py(CNFE_CLASS_EXTRACT, str(logcat))
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["classes"],
                [
                    "com.bytedance.android.live.base.model.message.EntertainmentPaidData",
                    "com.bytedance.android.live.base.model.message.HotWord",
                ],
            )
            self.assertGreaterEqual(len(payload["matches"]), 3)

    def test_extract_cnfe_classes_filters_package_and_start_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logcat = Path(tmpdir) / "logcat.txt"
            logcat.write_text(
                textwrap.dedent(
                    """\
                    04-21 12:00:00.090  4444  4444 E AndroidRuntime: Process: com.example.other, PID: 4444
                    04-21 12:00:00.091  4444  4444 E AndroidRuntime: Caused by: java.lang.ClassNotFoundException: com.example.other.HistoricalClass
                    04-21 12:00:00.092  1550  2365 I am_crash: [17777,0,com.example.other,951598660,java.lang.ClassNotFoundException,com.example.other.CrashClass,SourceFile,195,0]
                    04-21 12:00:00.100  5555  5555 E AndroidRuntime: Process: com.ss.android.ugc.live, PID: 5555
                    04-21 12:00:00.101  5555  5555 E AndroidRuntime: Caused by: java.lang.NoClassDefFoundError: Failed resolution of: Lcom/bytedance/android/live/base/model/message/HotWord;
                    04-21 12:00:00.102  1550  2365 I am_crash: [17891,0,com.ss.android.ugc.live,951598660,java.lang.ClassNotFoundException,com.bytedance.android.live.base.model.message.EntertainmentPaidData,SourceFile,195,0]
                    """
                ),
                encoding="utf-8",
            )
            result = self.run_py(
                CNFE_CLASS_EXTRACT,
                str(logcat),
                "--package",
                "com.ss.android.ugc.live",
                "--start-line",
                "4",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["classes"],
                [
                    "com.bytedance.android.live.base.model.message.EntertainmentPaidData",
                    "com.bytedance.android.live.base.model.message.HotWord",
                ],
            )
            self.assertEqual([item["line_no"] for item in payload["matches"]], [5, 6])
            self.assertEqual({item["kind"] for item in payload["matches"]}, {"failed_resolution", "am_crash"})

    def test_vdexdump_strict_rejects_structurally_invalid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_vdex = Path(tmpdir) / "bad.vdex"
            blob = struct.pack("<4s4sI", b"vdex", b"027\0", 1)
            blob += struct.pack("<III", 0, 16, 2)
            blob += b"\x00\x00"
            bad_vdex.write_bytes(blob)

            result = self.run_py(VDEXDUMP_MIN, str(bad_vdex), "--json", check=False)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["structurally_valid"])
            self.assertTrue(payload["issues"])

            strict = self.run_py(VDEXDUMP_MIN, str(bad_vdex), "--json", "--strict", check=False)
            self.assertNotEqual(strict.returncode, 0)

    def test_oat_artifact_manifest_summarizes_stable_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = Path(tmpdir) / "snapshot"
            snapshot.mkdir()
            (snapshot / "pm_art_dump.txt").write_text(
                textwrap.dedent(
                    """\
                    Package [com.ss.android.ugc.live]
                      arm64: [status=speed-profile] [reason=cmdline] [primary-abi]
                        [location is /data/app/pkg/oat/arm64/base.odex]
                    """
                ),
                encoding="utf-8",
            )
            (snapshot / "oat__arm64__base.odex.header.txt").write_text(
                textwrap.dedent(
                    """\
                    bootclasspath-checksums = i;14/ecbf80d4:i;32/71de3850
                    compiler-filter = speed-profile
                    dex2oat-cmdline = /apex/com.android.art/bin/dex2oat64 --class-loader-context=PCL[]{PCL[/system/framework/org.apache.http.legacy.jar]} --compiler-filter=speed-profile --compilation-reason=cmdline --instruction-set=arm64 --instruction-set-features=default --instruction-set-variant=cortex-a55
                    """
                ),
                encoding="utf-8",
            )
            (snapshot / "oat__arm64__base.vdex.json").write_text(
                json.dumps(
                    {
                        "magic": "vdex",
                        "version": "027",
                        "number_of_sections": 4,
                        "structurally_valid": True,
                        "issues": [],
                        "checksum_section": {"entry_count": 55},
                        "dex_section": {"embedded_dexes": [{"index": 0}, {"index": 1}]},
                        "verifier_deps_section": {"size": 1234},
                        "type_lookup_table_section": {"size": 5678},
                    }
                ),
                encoding="utf-8",
            )
            manifest = Path(tmpdir) / "manifest.json"
            self.run_py(
                OAT_ARTIFACT_MANIFEST,
                "--snapshot-dir",
                str(snapshot),
                "--requested-filter",
                "speed-profile",
                "--requested-reason",
                "cmdline",
                "--out",
                str(manifest),
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["effective"]["entries"][0]["status"], "speed-profile")
            self.assertTrue(payload["overall_structurally_valid"])
            self.assertEqual(payload["artifacts"]["oat__arm64__base.odex"]["stable_anchors"]["compiler_filter"], "speed-profile")
            self.assertEqual(payload["artifacts"]["oat__arm64__base.vdex"]["stable_anchors"]["checksum_entry_count"], 55)

    def test_oat_artifact_manifest_marks_failed_validators_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot = Path(tmpdir) / "snapshot"
            snapshot.mkdir()
            (snapshot / "pm_art_dump.txt").write_text(
                textwrap.dedent(
                    """\
                    Package [com.ss.android.ugc.live]
                      arm64: [status=speed-profile] [reason=cmdline] [primary-abi]
                        [location is /data/app/pkg/oat/arm64/base.odex]
                    """
                ),
                encoding="utf-8",
            )
            (snapshot / "oat__arm64__base.odex.header.txt").write_text(
                "compiler-filter = speed-profile\n"
                "dex2oat-cmdline = /apex/com.android.art/bin/dex2oat64 --compiler-filter=speed-profile --compilation-reason=cmdline\n",
                encoding="utf-8",
            )
            (snapshot / "oat__arm64__base.odex.header.rc").write_text("0\n", encoding="utf-8")
            (snapshot / "oat__arm64__base.vdex.rc").write_text("2\n", encoding="utf-8")
            manifest = Path(tmpdir) / "manifest.json"
            self.run_py(
                OAT_ARTIFACT_MANIFEST,
                "--snapshot-dir",
                str(snapshot),
                "--requested-filter",
                "speed-profile",
                "--requested-reason",
                "cmdline",
                "--out",
                str(manifest),
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertTrue(payload["effective_filter_matches_request"])
            self.assertTrue(payload["effective_reason_matches_request"])
            self.assertFalse(payload["overall_structurally_valid"])
            self.assertEqual(payload["artifacts"]["oat__arm64__base.odex"]["validation"]["oatdump_rc"], 0)
            self.assertEqual(payload["artifacts"]["oat__arm64__base.vdex"]["validation"]["vdexdump_rc"], 2)
            self.assertIn("vdexdump_rc exited with rc=2", payload["artifacts"]["oat__arm64__base.vdex"]["issues"])

    def test_stable_speed_profile_defaults_are_pinned_in_shell_scripts(self) -> None:
        capture_text = CAPTURE_SCRIPT.read_text(encoding="utf-8")
        regen_text = REGEN_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('FILTERS="speed-profile"', capture_text)
        self.assertIn('LAUNCH_INTERVAL=3', capture_text)
        self.assertIn('FOREGROUND_HOLD=5', capture_text)
        self.assertIn('POST_COMPILE_COLD_START_DELAYS="0,5"', capture_text)
        self.assertIn('FILTERS="speed-profile"', regen_text)

    def test_oatdump_probe_mode_and_capture_plumbing_exist(self) -> None:
        capture_text = CAPTURE_SCRIPT.read_text(encoding="utf-8")
        oatdump_text = OATDUMP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('--mode', oatdump_text)
        self.assertIn('--class-filter', oatdump_text)
        self.assertIn('--require-match', oatdump_text)
        self.assertIn('PROBE_CLASSES_FILE', capture_text)
        self.assertIn('extract_cnfe_classes.py', capture_text)
        self.assertIn('--package "$PKG"', capture_text)
        self.assertIn('--start-line', capture_text)
        self.assertIn('list-classes', capture_text)
        self.assertIn('mapfile -t probe_classes <', capture_text)
        self.assertNotIn('merge_probe_class_files "$PROBE_CLASSES_FILE" "$PROBE_CLASSES_FILE" "$runtime_txt"', capture_text)

    def test_tracefs_pid_discovery_is_root_aware_and_warns_on_unfiltered_scope(self) -> None:
        capture_text = CAPTURE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('proc_discovery_sh()', capture_text)
        self.assertIn('adb_su_sh "$cmd"', capture_text)
        self.assertIn('trace_scope_warning=still_unfiltered', capture_text)
        self.assertIn('found_pid="$(find_first_named_pid dex2oat64)"', capture_text)

    def test_shell_scripts_parse_cleanly(self) -> None:
        for script in (CAPTURE_SCRIPT, REGEN_SCRIPT, FREEZE_SCRIPT):
            result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, msg=f"{script} failed bash -n: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
