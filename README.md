# Retro FLP Rescue

## Overview
- `check_flp.py` performs a double-pass raw read of a 1.44 MB floppy disk, tracking each cylinder/head pair for stability and keeping the first successful read for the cloned image.
- After scanning, it prompts the user for a comment to tag the archive, then writes a JSON report, compresses the raw image and report into a ZIP, and removes intermediate files.

## Requirements
- Python 3.8+ with only standard-library modules (`os`, `time`, `json`, `struct`, `hashlib`, `sys`, `zipfile`, `datetime`, etc.).
- Windows: administrator privileges (needed to open `\\.\A:`-style raw device paths).
- macOS: a USB floppy drive; you will typically need `sudo` to open `/dev/rdiskN`.

## Usage
### Windows
1. Launch an elevated PowerShell session and `cd` into this repository.
2. Run `python check_flp.py` (defaults to `\\.\A:`). To specify a different source:
   - `python check_flp.py --source \\\\.\\A:`

### macOS
1. Attach your USB floppy drive and insert the disk.
2. List disks to find the floppy device:
   - `python3 check_flp.py --list`
3. Run against the raw device (typical):
   - `sudo python3 check_flp.py --source /dev/rdiskN`
4. Alternatively, you can pass the mounted volume path and the script will resolve it to a device:
   - `sudo python3 check_flp.py --source /Volumes/NAME`

### Disk images
- You can also run against a `.img/.bin` floppy image file:
  - `python3 check_flp.py --source path/to/floppy.img`

### Notes
- Use `--passes N` to change the number of read passes.
- Use `--comment TEXT` to avoid the interactive prompt.
- The script prints a progress summary (percentage, elapsed/remaining time, ETA) for each pass.
- Look inside the `BAK/` directory for the generated ZIP archive, which bundles the `.bin` image and `.json` metadata.

## Output Files
- ZIP files are named as:
  ```
  {timestamp}_{comment}_{md5part}_{health}.zip
  ```
  where `md5part` is derived from the image MD5 and `health` reflects how many of 160 tracks were recoverable.
- Extracting a ZIP yields:
  - `{base}.bin`: the reconstructed disk image.
  - `{base}.json`: report metadata (timestamp, serial, label, per-head stability, detected files, etc.).
- Temporary `.bin` and `.json` files are deleted after successful compression, leaving only the ZIP.

## Tips & Caveats
- Do not remove the floppy or power off the drive while scanning is in progress to avoid incomplete data.
- If the script fails to open the target device, double-check permissions (Windows: administrator; macOS: `sudo`) and that the specified device exists.
- The `BAK/` directory is created automatically. Archive or back it up as needed to preserve rescued disks.
