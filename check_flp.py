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

def enforce_cooldown(bak_dir: str, cooldown_minutes: int = 10):
    """检测上次备份时间，如果间隔不足则进行强制倒计时"""
    if not os.path.exists(bak_dir):
        return

    # 获取 BAK 目录下所有的 zip 文件
    zip_files = [os.path.join(bak_dir, f) for f in os.listdir(bak_dir) if f.endswith(".zip")]
    if not zip_files:
        return

    # 找到最新生成的文件
    latest_file = max(zip_files, key=os.path.getmtime)
    last_mod_time = os.path.getmtime(latest_file)
    
    elapsed_seconds = time.time() - last_mod_time
    required_seconds = cooldown_minutes * 60
    wait_seconds = required_seconds - elapsed_seconds

    if wait_seconds > 0:
        print(f"🛑 [保护机制] 软驱冷却中...")
        print(f"上次读取时间: {datetime.fromtimestamp(last_mod_time).strftime('%H:%M:%S')}")
        print(f"策略设定: 每 {cooldown_minutes} 分钟仅允许读取一张盘以防止磁头磨损。")
        
        try:
            while wait_seconds > 0:
                mins, secs = divmod(int(wait_seconds), 60)
                # 使用 \r 实现原地刷新倒计时
                sys.stdout.write(f"\r⏳ 强制冷却倒计时: {mins:02d}分{secs:02d}秒 后允许继续... (按 Ctrl+C 可强制跳过) ")
                sys.stdout.flush()
                time.sleep(1)
                wait_seconds -= 1
            print("\n✅ 冷却完成，准备开始读取。")
        except KeyboardInterrupt:
            print("\n⚠️ 警告: 用户手动跳过了冷却。请注意软驱发热情况！")

# --- 原有辅助函数保持不变 ---

def _is_probably_device_path(path: str) -> bool:
    if not path: return False
    return path.startswith("\\\\.\\") or path.startswith("/dev/")

def _sanitize_comment(comment: str) -> str:
    comment = (comment or "").strip()
    if not comment: return "NOCOMM"
    safe = [ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in comment]
    return "".join(safe).strip("_")[:32]

def _try_run(cmd: List[str]) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"

def _resolve_macos_device_from_mount(mount_path: str) -> Optional[str]:
    rc, out, _ = _try_run(["df", "-P", mount_path])
    if rc != 0 or not out.strip(): return None
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2: return None
    dev = lines[1].split()[0].strip()
    if dev.startswith("/dev/disk"): return dev.replace("/dev/disk", "/dev/rdisk", 1)
    return dev if dev.startswith("/dev/rdisk") else None

def _auto_detect_macos_floppy_device() -> Optional[str]:
    rc, out, _ = _try_run(["diskutil", "list"])
    if rc != 0 or not out.strip(): return None
    candidates: List[str] = []
    for line in out.splitlines():
        if any(k in line.upper() for k in ["FAT_12", "FAT12", "DOS_FAT_12", "FLOPPY", "1.4 MB"]):
            parts = line.strip().split()
            if parts and parts[-1].startswith("disk"):
                candidates.append("/dev/rdisk" + parts[-1][len("disk"):])
    return candidates[0] if candidates else None

def resolve_source(source: Optional[str]) -> str:
    system = platform.system()
    if source and source.lower() != "auto":
        source = os.path.expanduser(source)
        if system == "Darwin":
            if os.path.isdir(source):
                dev = _resolve_macos_device_from_mount(source)
                if dev: return dev
            if source.startswith("/dev/disk"): return source.replace("/dev/disk", "/dev/rdisk", 1)
        return source
    if system == "Windows": return r"\\.\A:"
    if system == "Darwin":
        dev = _auto_detect_macos_floppy_device()
        if dev: return dev
        raise SystemExit("Could not auto-detect a floppy device on macOS.")
    return "/dev/fd0"

def open_source(path: str):
    buffering = 0 if _is_probably_device_path(path) else -1
    return open(path, "rb", buffering=buffering)

# --- 修改后的主救援函数 ---

def multi_pass_rescue(
    drive_path: Optional[str] = None,
    *,
    passes: int = 2,
    comment: Optional[str] = None,
    cooldown: int = 10
):
    # 【新增】执行冷却检查
    enforce_cooldown(BAK_DIR, cooldown)

    # Standard 1.44MB Floppy Parameters
    TRACKS, HEADS = 80, 2
    SECTORS_PER_TRACK = 18
    SECTOR_SIZE = 512
    TRACK_SIZE = SECTORS_PER_TRACK * SECTOR_SIZE
    TOTAL_SIZE = 1474560
    TOTAL_TRACK_HEADS = TRACKS * HEADS
    TOTAL_OPS = TOTAL_TRACK_HEADS * passes

    drive_path = resolve_source(drive_path)

    now = datetime.now()
    report = {
        "timestamp_str": now.strftime("%Y-%m-%d %H:%M:%S"),
        "filename_ts": now.strftime("%Y%m%d%H%M%S"),
        "metadata": {"serial": "UNKNOWN", "label": "NO_LABEL", "fs": "FAT12"},
        "unstable_spots": [],
        "stable_bad_spots": [],
        "disk_map": []
    }
    
    master_bin = bytearray(TOTAL_SIZE)
    track_results = [[[] for _ in range(HEADS)] for _ in range(TRACKS)]

    print(f"\n🚀 [Rescue Mode] Target: {drive_path} | Passes: {passes}")
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
            for p in range(1, passes + 1):
                f.seek(0)
                for t in range(TRACKS):
                    for h in range(HEADS):
                        offset = (t * HEADS + h) * TRACK_SIZE
                        ops_done += 1
                        
                        # UI Progress Update
                        elapsed_sec = time.time() - start_time
                        avg_time = elapsed_sec / ops_done
                        remaining_sec = (TOTAL_OPS - ops_done) * avg_time
                        eta_dt = datetime.now() + timedelta(seconds=remaining_sec)

                        sys.stdout.write(
                            f"\rPass {p}/{passes} | {int((ops_done/TOTAL_OPS)*100):3d}% | "
                            f"T:{t:02d} H:{h} | ETA: {eta_dt.strftime('%H:%M:%S')} "
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
                
                if p < passes:
                    print(f"\nPass {p} complete. Brief motor pause...")
                    time.sleep(1) 

    except Exception as e:
        print(f"\n❌ Hardware Error: {e}")
        return

    # 3. Analysis
    final_status_map = []
    for t in range(TRACKS):
        for h in range(HEADS):
            res = track_results[t][h]
            successes = sum(res)
            if successes == passes: final_status_map.append("STABLE_OK")
            elif successes == 0:
                final_status_map.append("STABLE_BAD")
                report["stable_bad_spots"].append(f"T:{t:02d} H:{h}")
            else:
                final_status_map.append("UNSTABLE")
                report["unstable_spots"].append(f"T:{t:02d} H:{h} ({successes}/{passes})")

    # 4. Finalizing
    print("\n" + "-"*85)
    comment = _sanitize_comment(comment if comment is not None else input("Enter comment for archive: "))
    md5_hash = hashlib.md5(master_bin).hexdigest()
    md5_part = f"{md5_hash[:4]}{md5_hash[-4:]}".upper()
    salvaged_count = sum(1 for s in final_status_map if s != "STABLE_BAD")
    health_score = int(round((salvaged_count / float(TOTAL_TRACK_HEADS)) * 100))
    
    file_base = f"{report['filename_ts']}_{comment}_{md5_part}_{health_score}"
    os.makedirs(BAK_DIR, exist_ok=True)
    report["disk_map"] = final_status_map

    tmp_bin = os.path.join(BAK_DIR, f"{file_base}.bin")
    tmp_json = os.path.join(BAK_DIR, f"{file_base}.json")
    zip_path = os.path.join(BAK_DIR, f"{file_base}.zip")

    with open(tmp_bin, "wb") as fb: fb.write(master_bin)
    with open(tmp_json, "w", encoding='utf-8') as fj: json.dump(report, fj, indent=4, ensure_ascii=False)

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(tmp_bin, arcname=f"{file_base}.bin")
            archive.write(tmp_json, arcname=f"{file_base}.json")
        os.remove(tmp_bin)
        os.remove(tmp_json)
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
    print(f"\n📂 Saved to: {os.path.basename(zip_path)}")
    print("="*85)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="auto")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--comment", default=None)
    parser.add_argument("--cooldown", type=int, default=3, help="Wait minutes between disks")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list and platform.system() == "Darwin":
        rc, out, err = _try_run(["diskutil", "list"])
        sys.stdout.write(out or ""); sys.stderr.write(err or ""); raise SystemExit(rc)

    multi_pass_rescue(args.source, passes=args.passes, comment=args.comment, cooldown=args.cooldown)