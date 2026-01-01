import os
import time
import json
import struct
import hashlib
import sys
import zipfile
from datetime import datetime, timedelta

# Path Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BAK_DIR = os.path.join(SCRIPT_DIR, "BAK")

def format_time(seconds):
    """Utility to convert seconds to HH:MM:SS format"""
    return str(timedelta(seconds=int(seconds)))

def multi_pass_rescue(drive_path=r'\\.\A:'):
    # Standard 1.44MB Floppy Parameters
    TRACKS, HEADS = 80, 2
    SECTORS_PER_TRACK = 18
    SECTOR_SIZE = 512
    TRACK_SIZE = SECTORS_PER_TRACK * SECTOR_SIZE
    TOTAL_SIZE = 1474560
    PASSES = 2 
    TOTAL_OPS = TRACKS * HEADS * PASSES

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
    print("-" * 85)
    
    start_time = time.time()
    ops_done = 0

    try:
        with open(drive_path, 'rb') as f:
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
    comment = input("Enter comment for archive: ").strip() or "NOCOMM"
    md5_hash = hashlib.md5(master_bin).hexdigest()
    md5_part = f"{md5_hash[:4]}{md5_hash[-4:]}".upper()
    salvaged_count = sum(1 for s in final_status_map if s != "STABLE_BAD")
    health_score = int(round((salvaged_count / 160.0) * 100))
    
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

    render_final_view(report, final_status_map, zip_path, salvaged_count, health_score)

def render_final_view(report, status_map, zip_path, recovered, health):
    print("\n" + "="*85)
    print(f"💽 Archiving Summary ({report['timestamp_str']})")
    print(f"SN: {report['metadata']['serial']} | Salvaged: {recovered}/160 Tracks")
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
    multi_pass_rescue()