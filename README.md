# Retro FLP Rescue

## Overview
- `check_flp.py` performs a double-pass raw read of a 1.44 MB floppy disk, tracking each cylinder/head pair for stability and keeping the first successful read for the cloned image.
- After scanning, it prompts the user for a comment to tag the archive, then writes a JSON report, compresses the raw image and report into a ZIP, and removes intermediate files.

## Requirements
- Windows with administrator privileges (needed to open `\\.\A:`-style raw device paths).
- Python 3.8+ with only standard-library modules (`os`, `time`, `json`, `struct`, `hashlib`, `sys`, `zipfile`, `datetime`).

## Usage
1. Launch an elevated PowerShell session and `cd` into this repository.
2. Run `python check_flp.py`. By default it reads `A:`; to change the target drive, edit the final call:
   ```python
   multi_pass_rescue(drive_path=r'\\.\A:')
   ```
   or wrap the logic in argument parsing if desired.
3. The script prints a progress summary (percentage, elapsed/remaining time, ETA) for each pass. When scanning finishes you will be prompted to enter a short comment that is baked into the archive name.
4. Look inside the `BAK/` directory for the generated ZIP archive, which bundles the `.bin` image and `.json` metadata. The console also displays the final health percentage, visual status per head, and the archive path.

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
- If the script fails to open the target device, double-check you are running as administrator and that the specified drive exists.
- The `BAK/` directory is created automatically. Archive or back it up as needed to preserve rescued disks.
