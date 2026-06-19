# Spool RFID Tag Writer

Writes an [OpenSpool](https://github.com/spuder/OpenSpool) JSON payload to an NFC tag (NTAG213/215/216) using an ACR122U reader, for use with [Spoolman](https://github.com/Donkie/Spoolman) and the Snapmaker U1 / OpenSpool ecosystem.

This avoids `nfcpy` and `pyscard` entirely. The script (`write_tag_ctypes.py`) talks to Windows' built-in `winscard.dll` directly via Python's `ctypes` standard library module, so there's **nothing to pip install and nothing to compile**. It uses the same PC/SC smart card service Windows already runs for ID cards, the ACR122U's stock CCID driver, etc.

## Files

| File | Purpose |
|---|---|
| `write_tag_ctypes.py` | The writer script. Reads a JSON file and writes it to a tag as an NDEF record. |
| `spool.example.json` | A sample/reference tag payload showing all supported fields. Copy and edit this for your own spools. |

## Requirements

- Windows 10/11
- Python 3.x (any version, no compiler or extra packages needed)
- ACR122U USB NFC reader, using its **default CCID driver** (the one Windows installs automatically). If you previously tried this with `nfcpy` and used Zadig to switch the driver to WinUSB/libusb-win32, you need to revert that first:
  1. Open **Device Manager**
  2. Find the ACR122U (it may show under "Smart card readers" or "libusb-win32 devices" if it was switched)
  3. Right-click → **Uninstall device**
  4. Unplug and replug the reader so Windows reinstalls the default driver
- Windows **Smart Card** service running (`services.msc` → "Smart Card" → Start/Automatic)
- A blank or rewritable NTAG213/215/216 tag

## Usage

1. Edit `spool.example.json` (or make a copy) with your spool's details.
2. Run the script, pointing it at your JSON file:

   ```
   python write_tag_ctypes.py spools/spool.example.json
   ```

3. When prompted, place the tag on the reader. The script polls for up to 15 seconds, so you don't need to have it positioned beforehand.
4. You should see each page write successfully:

   ```
   Writing 218 bytes starting at page 4...
     Write page 4: OK (SW=9000)
     Write page 5: OK (SW=9000)
     ...
   ✅ Done. Tag written successfully.
   ```

5. Verify the tag by reading it back, either with the [printtag-web](https://printtag-web.pages.dev/) "Read Tag" function on an Android phone (Chrome supports Web NFC), or with a generic app like **NFC Tools** (Android/iOS).

## JSON field reference

See `spool.example.json` for a working sample. Fields:

| Field | Description |
|---|---|
| `protocol` | Must stay `"openspool"`. Identifies the tag format. |
| `version` | Schema version for this tag format. Currently `"1.0"`. |
| `type` | Filament material, e.g. `PLA`, `PETG`, `ABS`, `TPU`. |
| `subtype` | Optional finish/variant, e.g. `"Matte"`, `"Silk"`, `"Shiny"`. Leave as `""` if not applicable. |
| `color_hex` | Filament color as a 6-digit hex RGB string, no leading `#` (e.g. `b3b3b3`). |
| `brand` | Manufacturer/brand name. |
| `min_temp` / `max_temp` | Recommended nozzle temperature range, in °C. |
| `bed_min_temp` / `bed_max_temp` | Recommended bed temperature range, in °C. |
| `spool_id` | The Spoolman spool ID this tag is linked to. |

JSON itself doesn't support comments, so this table (rather than inline `//` comments) is the documentation source of truth. Don't add comments directly into `.json` files used by the script, they'll fail to parse.

## Tag capacity

NTAG213 tags have ~144 bytes of usable memory. A typical OpenSpool JSON payload (wrapped in its NDEF envelope) runs ~200+ bytes, which **will not fit on NTAG213**. Use **NTAG215** (504 bytes) or **NTAG216** (888 bytes) tags instead. The script will print a warning if your payload is too large for an NTAG213, but it doesn't currently block writing to a too-small tag, watch for write failures partway through if you ignore the warning.

## Troubleshooting

**"No PC/SC readers found"**
- Confirm the ACR122U is plugged in and its LED is lit.
- Check Device Manager for the reader (see Requirements above for driver notes).
- Confirm the Windows "Smart Card" service is running.

**Multiple readers found, wrong one picked**
- The script auto-prefers a reader with "ACR122" or "ACS" in its name. If you have other PC/SC devices (e.g. a YubiKey), confirm the printed "Using reader: ..." line shows the ACR122U, not something else.

**`SCardConnectW ... timed out waiting for tag`**
- Make sure the tag is centered on the reader's antenna (usually marked on the case) and stays still while writing.
- Try a different tag, some can be defective or already locked/write-protected.

**Write fails partway through (e.g. `Write page 5: FAIL`)**
- Most often means the tag ran out of usable memory. Switch to an NTAG215/216 (see Tag capacity above).
- Could also mean the tag moved off the reader mid-write. Hold it steady until you see "Done."

## Why ctypes instead of pyscard/nfcpy

- `nfcpy` requires raw USB access via `libusb`, which on Windows means manually swapping the ACR122U's driver using Zadig. This is fragile and breaks the reader's normal CCID/PC/SC mode.
- `pyscard` only ships prebuilt wheels for specific Python versions (currently up to 3.13). On newer or mismatched Python versions, `pip install pyscard` falls back to compiling from source, requiring Visual C++ Build Tools.
- `winscard.dll` is part of Windows itself. Calling it via `ctypes` (part of Python's standard library) means zero extra installs and zero compiler dependency, at the cost of slightly more verbose code.