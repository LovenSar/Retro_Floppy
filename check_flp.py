import os
import time
import json
import struct
import hashlib
import sys
import zipfile
import argparse
import platform
import subprocess
from typing import List, Optional, Tuple
from datetime import datetime, timedelta

# Path Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BAK_DIR = os.path.join(SCRIPT_DIR, "BAK")

def format_time(seconds):
    """Utility to convert seconds to HH:MM:SS format"""
    return str(timedelta(seconds=int(seconds)))

def _is_probably_device_path(path: str) -> bool:
    if not path:
        return False
    if path.startswith("\\\\.\\"):
        return True
    if path.startswith("/dev/"):
        return True
    return False

def _sanitize_comment(comment: str) -> str:
    comment = (comment or "").strip()
    if not comment:
        return "NOCOMM"
    safe = []
    for ch in comment:
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        else:
            safe.append("_")
    cleaned = "".join(safe).strip("_")
    return (cleaned or "NOCOMM")[:32]

def _try_run(cmd: List[str]) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"

def _resolve_macos_device_from_mount(mount_path: str) -> Optional[str]:
    rc, out, _ = _try_run(["df", "-P", mount_path])
    if rc != 0 or not out.strip():
        return None
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    dev = lines[1].split()[0].strip()
    if dev.startswith("/dev/disk"):
        return dev.replace("/dev/disk", "/dev/rdisk", 1)
    if dev.startswith("/dev/rdisk"):
        return dev
    return None

def _auto_detect_macos_floppy_device() -> Optional[str]:
    rc, out, _ = _try_run(["diskutil", "list"])
    if rc != 0 or not out.strip():
        return None

    # Heuristics:
    # - Some USB floppy drives show partitions as DOS_FAT_12 / FAT12
    # - Size is typically ~1.4 MB
    candidates: List[str] = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        upper = s.upper()
        if "FAT_12" in upper or "FAT12" in upper or "DOS_FAT_12" in upper or "FLOPPY" in upper or "1.4 MB" in upper:
            parts = s.split()
            if parts:
                last = parts[-1]
                if last.startswith("disk"):
                    candidates.append("/dev/rdisk" + last[len("disk"):])

    return candidates[0] if candidates else None

def resolve_source(source: Optional[str]) -> str:
    system = platform.system()
    if source and source.lower() != "auto":
        source = os.path.expanduser(source)
        if system == "Darwin" and os.path.isdir(source):
            dev = _resolve_macos_device_from_mount(source)
            if dev:
                return dev
        if system == "Darwin" and source.startswith("/dev/disk"):
            return source.replace("/dev/disk", "/dev/rdisk", 1)
        return source

    if system == "Windows":
        return r"\\.\A:"
    if system == "Darwin":
        dev = _auto_detect_macos_floppy_device()
        if dev:
            return dev
        raise SystemExit(
            "Could not auto-detect a floppy device on macOS. "
            "Run with `--list` and then provide `--source /dev/rdiskN` (or `--source /Volumes/NAME`)."
        )
    # Linux / other Unix
    return "/dev/fd0"

def open_source(path: str):
    buffering = 0 if _is_probably_device_path(path) else -1
    return open(path, "rb", buffering=buffering)

def multi_pass_rescue(
    drive_path: Optional[str] = None,
    *,
    passes: int = 2,
    comment: Optional[str] = None,
):
    # Standard 1.44MB Floppy Parameters
    TRACKS, HEADS = 80, 2
    SECTORS_PER_TRACK = 18
    SECTOR_SIZE = 512
    TRACK_SIZE = SECTORS_PER_TRACK * SECTOR_SIZE
    TOTAL_SIZE = 1474560
    PASSES = int(passes)
    TOTAL_TRACK_HEADS = TRACKS * HEADS
    TOTAL_OPS = TOTAL_TRACK_HEADS * PASSES

    drive_path = resolve_source(drive_path)

    now = datetime.now()
    report = {
        "timestamp_str": now.strftime("%Y-%m-%d %H:%M:%S"),
        "filename_ts": now.strftime("%Y%m%d%H%M%S"),
        "metadata": {"serial": "UNKNOWN", "label": "NO_LABEL", "fs": "FAT12"},
        "unstable_spots": [],
        "stable_bad_spots": [],
        "found_files": []
    }
    
    master_bin = bytearray(TOTAL_SIZE)
    track_results = [[[] for _ in range(HEADS)] for _ in range(TRACKS)]

    print(f"🚀 [Double-Pass Rescue] Target: {drive_path} | Total Passes: {PASSES}")
    if platform.system() == "Darwin" and drive_path.startswith("/dev/rdisk"):
        print("ℹ️  macOS raw device detected; if open fails, try: sudo python3 check_flp.py --source /dev/rdiskN")
    print("-" * 85)
    
    start_time = time.time()
    ops_done = 0

    try:
        with open_source(drive_path) as f:
            # 1. Metadata Extraction
            try:
                boot = f.read(512)
                if len(boot) == 512:
                    sn = struct.unpack("<I", boot[39:43])[0]
                    report["metadata"]["serial"] = f"{sn:08X}"
                    label = boot[43:54].decode('ascii', errors='ignore').strip()
                    if label: report["metadata"]["label"] = label
            except: pass

            # 2. Scanning Passes
            for p in range(1, PASSES + 1):
                f.seek(0)
                for t in range(TRACKS):
                    for h in range(HEADS):
                        offset = (t * HEADS + h) * TRACK_SIZE
                        
                        ops_done += 1
                        elapsed_sec = time.time() - start_time
                        avg_time_per_op = elapsed_sec / ops_done
                        remaining_ops = TOTAL_OPS - ops_done
                        remaining_sec = remaining_ops * avg_time_per_op
                        eta_dt = datetime.now() + timedelta(seconds=remaining_sec)

                        sys.stdout.write(
                            f"\rPass {p}/{PASSES} | {int((ops_done/TOTAL_OPS)*100):3d}% | "
                            f"T:{t:02d} H:{h} | "
                            f"Elapsed: {format_time(elapsed_sec)} | "
                            f"Remain: {format_time(remaining_sec)} | "
                            f"ETA: {eta_dt.strftime('%H:%M:%S')} "
                        )
                        sys.stdout.flush()

                        try:
                            f.seek(offset)
                            data = f.read(TRACK_SIZE)
                            if len(data) == TRACK_SIZE:
                                track_results[t][h].append(True)
                                if sum(track_results[t][h]) == 1:
                                    master_bin[offset:offset+TRACK_SIZE] = data
                            else:
                                track_results[t][h].append(False)
                        except:
                            track_results[t][h].append(False)
                
                if p < PASSES:
                    print(f"\nPass {p} complete. Stabilizing motor...")
                    time.sleep(1) 

    except PermissionError as e:
        print(f"\n❌ Permission Error: {e}")
        if platform.system() == "Darwin" and drive_path.startswith("/dev/"):
            print("Try: sudo python3 check_flp.py --source " + drive_path)
        return
    except Exception as e:
        print(f"\n❌ Hardware Error: {e}")
        return

    # 3. Analysis
    final_status_map = []
    for t in range(TRACKS):
        for h in range(HEADS):
            res = track_results[t][h]
            successes = sum(res)
            if successes == PASSES:
                final_status_map.append("STABLE_OK")
            elif successes == 0:
                final_status_map.append("STABLE_BAD")
                report["stable_bad_spots"].append(f"T:{t:02d} H:{h}")
            else:
                final_status_map.append("UNSTABLE")
                report["unstable_spots"].append(f"T:{t:02d} H:{h} ({successes}/{PASSES})")

    # 4. Finalizing and Archiving
    print("\n" + "-"*85)
    comment = _sanitize_comment(comment if comment is not None else input("Enter comment for archive: "))
    md5_hash = hashlib.md5(master_bin).hexdigest()
    md5_part = f"{md5_hash[:4]}{md5_hash[-4:]}".upper()
    salvaged_count = sum(1 for s in final_status_map if s != "STABLE_BAD")
    health_score = int(round((salvaged_count / float(TOTAL_TRACK_HEADS)) * 100))
    
    file_base = f"{report['filename_ts']}_{comment}_{md5_part}_{health_score}"
    os.makedirs(BAK_DIR, exist_ok=True)
    report["disk_map"] = final_status_map

    # Temporary paths for raw files
    tmp_bin = os.path.join(BAK_DIR, f"{file_base}.bin")
    tmp_json = os.path.join(BAK_DIR, f"{file_base}.json")
    zip_path = os.path.join(BAK_DIR, f"{file_base}.zip")

    # Write temporary files
    with open(tmp_bin, "wb") as fb:
        fb.write(master_bin)
    with open(tmp_json, "w", encoding='utf-8') as fj:
        json.dump(report, fj, indent=4, ensure_ascii=False)

    # 5. ZIP Compression Logic
    print(f"Compressing archive to ZIP...")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(tmp_bin, arcname=f"{file_base}.bin")
            archive.write(tmp_json, arcname=f"{file_base}.json")
        
        # Remove temporary files after successful compression
        os.remove(tmp_bin)
        os.remove(tmp_json)
        print(f"Successfully compressed and cleaned up temporary files.")
    except Exception as ze:
        print(f"❌ Compression Error: {ze}")

    render_final_view(report, final_status_map, zip_path, salvaged_count, TOTAL_TRACK_HEADS, health_score)

def render_final_view(report, status_map, zip_path, recovered, total_tracks, health):
    print("\n" + "="*85)
    print(f"💽 Archiving Summary ({report['timestamp_str']})")
    print(f"SN: {report['metadata']['serial']} | Salvaged: {recovered}/{total_tracks} Tracks")
    print("-" * 85)
    icons = {"STABLE_OK": "■", "UNSTABLE": "?", "STABLE_BAD": "░"}
    m = [icons[s] for s in status_map]
    print(f"Head 0: {''.join(m[0::2])}")
    print(f"Head 1: {''.join(m[1::2])}")
    print(f"Final Health: {health}%")
    
    # Simple display of captured ZIP path
    print(f"\n📂 Compressed Archive: {os.path.basename(zip_path)}")
    print(f"📍 Location: {os.path.dirname(zip_path)}")
    print("="*85)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Double-pass raw read/rescue of a 1.44MB floppy (or disk image).",
    )
    parser.add_argument(
        "--source",
        default="auto",
        help=r"Raw device path or image file. Windows: \\.\A:  macOS: /dev/rdiskN or /Volumes/NAME  Linux: /dev/fd0  (default: auto)",
    )
    parser.add_argument("--passes", type=int, default=2, help="Number of read passes (default: 2)")
    parser.add_argument("--comment", default=None, help="Archive comment (skips interactive prompt)")
    parser.add_argument(
        "--list",
        action="store_true",
        help="List likely devices (macOS only) and exit",
    )
    args = parser.parse_args()

    if args.list:
        if platform.system() == "Darwin":
            rc, out, err = _try_run(["diskutil", "list"])
            sys.stdout.write(out if out else "")
            sys.stderr.write(err if err else "")
            raise SystemExit(rc)
        print("--list is only supported on macOS.")
        raise SystemExit(2)

    multi_pass_rescue(args.source, passes=args.passes, comment=args.comment)
