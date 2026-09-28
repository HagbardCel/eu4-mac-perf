#!/usr/bin/env python3
"""One-launch OFF/ON/OFF/ON validation of paused-and-idle GOG EU IV pacing."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import mmap
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import eu4_benchmark as base
import eu4_diagnostic as diagnostic


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_suffix(".c")
LIBRARY = SOURCE.parent / ".build/libeu4_idle_pacer.dylib"
HARNESS = ROOT / "tests/idle_pacer_harness.cpp"
HARNESS_EXE = SOURCE.parent / ".build/idle_pacer_harness"
GOG_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PHASE_SECONDS = 25


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-dynamiclib", "-framework", "OpenGL", "-framework", "CoreGraphics",
         "-o", str(LIBRARY), str(SOURCE)],
        ["clang++", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-Wl,-export_dynamic", "-framework", "OpenGL", "-o", str(HARNESS_EXE),
         str(HARNESS)],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Could not build idle pacer: {result.stderr.strip()}")
    for path in (LIBRARY, HARNESS_EXE):
        if base.command("lipo", "-archs", str(path)).strip() != "x86_64":
            raise base.BenchmarkError(f"{path.name} is not x86_64")


def pacer_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    with path.open(newline="") as source:
        for row in csv.reader(source):
            if len(row) != 9 or row[0] != "S":
                continue
            try:
                interval_ns = int(row[4])
                if interval_ns <= 0:
                    continue
                rows.append({"clock_ns": int(row[1]), "wall_ns": int(row[2]),
                             "mode": int(row[3]), "interval_ns": interval_ns,
                             "swaps": int(row[5]), "eligible": int(row[6]),
                             "slept_ns": int(row[7]), "rejected": int(row[8])})
            except ValueError:
                continue
    return rows


def preflight() -> dict:
    identity = base.gog_identity()
    if identity["executable_sha256"] != GOG_SHA256:
        raise base.BenchmarkError("The GOG executable changed; the version-pinned pause-state offsets must be reviewed")
    build()
    symbols = base.command("nm", "-nm", str(base.GOG_EXE))
    for symbol in ("__ZN12CApplication14AccessInstanceEv",
                   "__ZNK10CGameSpeed16IsActuallyPausedEv", "__ZTV12CInGameIdler"):
        if symbol not in symbols:
            raise base.BenchmarkError(f"Version-pinned pause-state symbol is missing: {symbol}")
    with tempfile.TemporaryDirectory(prefix="eu4-pacer-preflight-") as temporary:
        root = Path(temporary)
        control, log = root / "control.bin", root / "pacer.csv"
        control.write_bytes(bytes(4096))
        env = os.environ.copy()
        env.update(DYLD_INSERT_LIBRARIES=str(LIBRARY), EU4_PACE_CONTROL=str(control),
                   EU4_PACE_LOG=str(log), EU4_PACE_TEST_IDLE="1")
        started = time.time_ns()
        result = subprocess.run([str(HARNESS_EXE)], env=env, capture_output=True,
                                text=True, check=False, timeout=20)
        ended = time.time_ns()
        if result.returncode:
            raise base.BenchmarkError(f"Offline pause/pacing harness failed ({result.returncode}): "
                                      f"{result.stderr.strip() or result.stdout.strip()}")
        rows = pacer_rows(log)
        off = [row for row in rows if row["mode"] == 0]
        on = [row for row in rows if row["mode"] == 1]
        if (len(off) < 2 or not on or sum(row["eligible"] for row in on) < 40 or
                sum(row["slept_ns"] for row in on) < 100_000_000 or
                sum(row["rejected"] for row in on) < 100 or
                not all(started-2_000_000_000 <= row["wall_ns"] <= ended+2_000_000_000
                        for row in rows)):
            raise base.BenchmarkError("Offline harness did not verify OFF passthrough and paused-idle ON pacing")
    return {"gog": identity, "library_sha256": base.sha256(LIBRARY),
            "harness": "passed", "off_snapshots": len(off), "on_snapshots": len(on),
            "on_eligible_swaps": sum(row["eligible"] for row in on),
            "on_slept_ms": round(sum(row["slept_ns"] for row in on)/1e6, 1),
            "on_rejected_swaps": sum(row["rejected"] for row in on)}


def mark(path: Path, event: str, **values: object) -> dict:
    row = {"event": event, "monotonic_ns": time.monotonic_ns(),
           "wall_ns": time.time_ns(), **values}
    with path.open("a") as output:
        output.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def summarize_swaps(rows: list[dict], phases: list[dict], anchor: dict) -> dict:
    if not rows:
        raise base.BenchmarkError("No pacer swap snapshots were written")
    clock_offset = statistics.median_low([row["wall_ns"]-row["clock_ns"] for row in rows])
    clock_offset += anchor["monotonic_ns"]-anchor["wall_ns"]
    result = {}
    for phase in phases:
        settling = 4_000_000_000 if phase["mode"] else 2_000_000_000
        selected = [row for row in rows
                    if row["mode"] == phase["mode"] and
                    phase["start_ns"]+settling <= row["clock_ns"]+clock_offset < phase["end_ns"]]
        result[phase["name"]] = {
            "snapshots": len(selected),
            "median_swaps_s": round(statistics.median(
                                  row["swaps"]*1e9/row["interval_ns"] for row in selected),2)
                              if selected else None,
            "eligible_swaps": sum(row["eligible"] for row in selected),
            "slept_ms": round(sum(row["slept_ns"] for row in selected)/1e6, 2),
            "rejected_swaps": sum(row["rejected"] for row in selected),
        }
    return result


def write_report(run_dir: Path, phases: list[dict], anchor: dict, game_pid: int) -> dict:
    swaps = summarize_swaps(pacer_rows(run_dir / "pacer.csv"), phases, anchor)
    power = diagnostic.summarize_power(base.read_plist_stream(
        run_dir / "powermetrics.pliststream"), phases, anchor, game_pid)
    output = {"phases": {name: {**item, "power": power[name]}
                         for name, item in swaps.items()}}
    off_names, on_names = ("off_1", "off_2"), ("on_1", "on_2")
    def med(names: tuple[str, str], field: str, power_field: bool = False) -> float | None:
        values = [(power if power_field else swaps)[name].get(field)
                  for name in names]
        return statistics.median(values) if all(isinstance(x, (int, float)) for x in values) else None
    comparisons = {}
    for field, power_field in (("median_swaps_s", False), ("combined_w", True),
                               ("cpu_w", True), ("gpu_w", True),
                               ("eu4_cputime_ms_per_s", True), ("gpu_active_pct", True)):
        before, after = med(off_names,field,power_field), med(on_names,field,power_field)
        comparisons[field] = {"off": before, "on": after,
                              "relative_change_pct": round(100*(after/before-1),2)
                              if before not in (None,0) and after is not None else None}
    output["comparison"] = comparisons
    output["quality"] = {
        "adequate_samples": all(swaps[name]["snapshots"] >= 16 and power[name].get("samples",0) >= 18
                                for name in swaps),
        "pacing_engaged": all(swaps[name]["eligible_swaps"] >= 100 and
                              swaps[name]["slept_ms"] >= 100 for name in on_names),
        "unpaused_or_active_swaps_during_on": sum(swaps[name]["rejected_swaps"] for name in on_names),
    }
    lines = ["# GOG EU IV paused-idle pacing validation", "",
             "OFF → ON → OFF → ON in one GOG EU IV launch. ON applies a 60/s swap pace only after the game reports paused and system input has been idle for three seconds.",
             "", "| Phase | Swap calls/s | CPU W | GPU W | Combined W | EU IV CPU ms/s | Eligible swaps | Slept ms |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ("off_1","on_1","off_2","on_2"):
        item=output["phases"][name]; p=item["power"]
        lines.append(f"| {name} | {item['median_swaps_s']} | {p.get('cpu_w','—')} | {p.get('gpu_w','—')} | {p.get('combined_w','—')} | {p.get('eu4_cputime_ms_per_s','—')} | {item['eligible_swaps']} | {item['slept_ms']} |")
    lines.extend(["", "## Paired medians", ""])
    for field, item in comparisons.items():
        lines.append(f"- {field}: OFF {item['off']}, ON {item['on']}, change {item['relative_change_pct']}%")
    lines.extend(["", "## Quality", "",
                  f"- Adequate aligned samples: {output['quality']['adequate_samples']}",
                  f"- Pacing engaged in both ON phases: {output['quality']['pacing_engaged']}",
                  f"- ON swaps rejected by pause/input gate: {output['quality']['unpaused_or_active_swaps_during_on']}",
                  "- Swap calls are an in-process proxy for frames; screen output and interaction smoothness require operator observation.",
                  "- Power is estimated system-wide CPU/GPU power; this within-launch comparison reduces but does not eliminate thermal and background-task drift.", ""])
    (run_dir / "validation.json").write_text(json.dumps(output, indent=2) + "\n")
    (run_dir / "validation.md").write_text("\n".join(lines))
    return output


def run(args: argparse.Namespace) -> None:
    evidence = preflight()
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        raise base.BenchmarkError("Close EU IV before the controlled GOG validation launch") from exc
    save = base.DEFAULT_SAVE
    if not save.is_file():
        raise base.BenchmarkError(f"Disposable Venice save is missing: {save}")
    save_hash = base.sha256(save)
    if subprocess.run(["sudo", "-v"], check=False).returncode:
        raise base.BenchmarkError("Administrator access is required for powermetrics")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or not isinstance(launcher.get("exeArgs"), list):
        raise base.BenchmarkError("Unexpected GOG launcher configuration")
    run_dir = Path(args.output).expanduser().resolve() / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-idle-pace"
    run_dir.mkdir(parents=True, exist_ok=False)
    control_path, events = run_dir / "control.bin", run_dir / "events.jsonl"
    control_path.write_bytes(bytes(4096))
    metadata = {"status": "launching", "gog": evidence["gog"],
                "preflight": evidence, "save_sha256": save_hash,
                "library_sha256": evidence["library_sha256"], "run_dir": str(run_dir)}
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    game = pm = None
    phases: list[dict] = []
    try:
        with control_path.open("r+b") as control_file, mmap.mmap(control_file.fileno(),4096) as control:
            env = os.environ.copy()
            env.pop("EU4_PACE_TEST_IDLE",None)
            env.update(EU4_PACE_LOG=str(run_dir/"pacer.csv"),
                       EU4_PACE_CONTROL=str(control_path), DYLD_INSERT_LIBRARIES=str(LIBRARY))
            with (run_dir/"game.stdout").open("wb") as stdout, (run_dir/"game.stderr").open("wb") as stderr:
                game = subprocess.Popen(["./eu4",*launcher["exeArgs"]], cwd=base.GOG_EXE.parent,
                                        env=env, stdout=stdout, stderr=stderr)
            metadata["game_pid"] = game.pid
            print("Launched the GOG EU IV build with the paused-idle pacer OFF.")
            print("Load the disposable Venice save, pause, and return to Terminal.")
            if base.prompt("Confirm the save and expected DLC/mods are loaded (yes/no): ",True).lower() != "yes":
                raise base.BenchmarkError("Game state was not confirmed")
            if game.poll() is not None or base.running_game_pid("paused") != game.pid:
                raise base.BenchmarkError("The expected GOG process is not running")
            if subprocess.run(["sudo","-v"],check=False).returncode:
                raise base.BenchmarkError("Administrator authorization expired before capture")
            print("ON phases pace paused, input-idle animation at up to 60 swaps/s; please note any visible loss of smoothness.")
            base.prompt("Keep EU IV paused. Press Enter, return to the game, and leave it untouched through five sounds: ")
            time.sleep(5)
            display, settings, power = base.display_mode(), base.game_settings(), base.power_state()
            if (not display.get("verified") or abs(display.get("refresh_hz",0)-120)>1 or
                settings.get("fullScreen") != "yes" or settings.get("borderless") != "no" or
                settings.get("game_resolution") != "3456x2234" or power.get("mode") != "normal"):
                raise base.BenchmarkError("Expected verified 120 Hz, 3456×2234 fullscreen, Normal power mode")
            metadata.update(display=display,settings=settings,power=power,dlc_mods=base.mod_settings())
            anchor = mark(events,"clock_anchor")
            metadata["clock_anchor"] = anchor
            command=["sudo","-n","powermetrics","-i","1000","-n","106",
                     "-s","tasks,cpu_power,gpu_power,thermal","-f","plist","--show-process-gpu"]
            with (run_dir/"powermetrics.pliststream").open("wb") as raw, \
                 (run_dir/"powermetrics.stderr").open("wb") as errors:
                pm=subprocess.Popen(command,stdout=raw,stderr=errors)
                for index,(name,mode) in enumerate((("off_1",0),("on_1",1),
                                                     ("off_2",0),("on_2",1))):
                    control[0]=mode
                    base.cue()
                    print(f"Phase {index+1}/4: {name.upper()} for {PHASE_SECONDS}s",flush=True)
                    start=mark(events,"phase_start",phase=name,mode=mode)
                    deadline=time.monotonic()+PHASE_SECONDS
                    checked_headroom=False
                    while time.monotonic()<deadline:
                        if game.poll() is not None:
                            raise base.BenchmarkError("GOG EU IV exited during validation")
                        if pm.poll() is not None and pm.returncode:
                            raise base.BenchmarkError("powermetrics exited during validation")
                        if name == "off_1" and not checked_headroom and \
                                time.monotonic_ns()-start["monotonic_ns"] >= 10_000_000_000:
                            checked_headroom=True
                            provisional={"name":name,"mode":mode,
                                         "start_ns":start["monotonic_ns"],
                                         "end_ns":time.monotonic_ns()}
                            baseline=summarize_swaps(pacer_rows(run_dir/"pacer.csv"),
                                                     [provisional],anchor)[name]
                            if (baseline["snapshots"] < 5 or
                                baseline["median_swaps_s"] is None or
                                baseline["median_swaps_s"] < 70):
                                raise base.BenchmarkError(
                                    f"Stopped early: OFF baseline {baseline['median_swaps_s']} swaps/s "
                                    "has insufficient headroom for a useful 60/s cap")
                        time.sleep(min(.5,max(0,deadline-time.monotonic())))
                    end=mark(events,"phase_end",phase=name)
                    phases.append({"name":name,"mode":mode,
                                   "start_ns":start["monotonic_ns"],"end_ns":end["monotonic_ns"]})
                    if name == "on_1":
                        partial=summarize_swaps(pacer_rows(run_dir/"pacer.csv"),phases,anchor)[name]
                        if partial["eligible_swaps"] < 100 or partial["slept_ms"] < 100:
                            raise base.BenchmarkError("Paused/idle pacing did not engage; check that EU IV stayed paused and input was idle")
                control[0]=0
                base.cue()
                try:
                    pm.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    pm.terminate(); pm.wait(timeout=5)
            if pm.returncode:
                raise base.BenchmarkError(f"powermetrics failed: {(run_dir/'powermetrics.stderr').read_text(errors='replace').strip()}")
            if base.sha256(save) != save_hash:
                raise base.BenchmarkError("Disposable save changed during paused validation")
            metadata["phases"] = phases
            report = write_report(run_dir,phases,anchor,game.pid)
            if not report["quality"]["adequate_samples"] or not report["quality"]["pacing_engaged"]:
                raise base.BenchmarkError("Validation lacks sufficient aligned or engaged samples; inspect raw logs")
            metadata["status"]="complete"
            print(f"Validation report: {run_dir/'validation.md'}")
            print("Quit the measured game session normally when finished.")
    except (base.BenchmarkError,OSError,KeyboardInterrupt) as exc:
        metadata["status"]="incomplete"
        metadata["error"]=str(exc)
        raise
    finally:
        # A failed validation must not leave the running game in ON mode.
        if control_path.is_file():
            with control_path.open("r+b") as control_file:
                control_file.write(b"\0")
                control_file.flush()
        if pm is not None and pm.poll() is None:
            pm.terminate()
        metadata["ended_utc"]=dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata,indent=2,default=str)+"\n")


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("preflight",help="verify the version-pinned pause gate and pacer in an offline x86_64 harness")
    collect=sub.add_parser("run",help="one controlled GOG EU IV paused OFF/ON/OFF/ON launch")
    collect.add_argument("--output",default=str(ROOT/"results"))
    analyze=sub.add_parser("report",help="regenerate a validation report")
    analyze.add_argument("run_dir")
    args=parser.parse_args()
    try:
        if args.command == "preflight":
            print(json.dumps(preflight(),indent=2))
        elif args.command == "run":
            run(args)
        else:
            path=Path(args.run_dir).expanduser().resolve()
            metadata=json.loads((path/"metadata.json").read_text())
            write_report(path,metadata["phases"],metadata["clock_anchor"],metadata["game_pid"])
            print(path/"validation.md")
    except (base.BenchmarkError,OSError,subprocess.SubprocessError,KeyboardInterrupt) as exc:
        print(f"Error: {exc}",file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
