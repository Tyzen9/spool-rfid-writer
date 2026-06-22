#!/usr/bin/env python3
"""
write_tag.py - Write an OpenSpool JSON file to an NFC tag (NTAG21x) using an
ACR122U reader, by calling Windows' built-in winscard.dll directly via ctypes.

No pip installs. No compiler. No driver swapping (Zadig). Uses only the
standard PC/SC smart card service that's already part of Windows.

Usage:
    python write_tag.py spool.json

Requirements:
    - Windows 10/11
    - ACR122U plugged in (using its default CCID driver, the one Windows
      installs automatically - do NOT use Zadig/WinUSB with this script)
    - Windows "Smart Card" service running (services.msc -> Smart Card -> Start)
"""

import sys
import json
import ctypes
from ctypes import wintypes

# ---------------------------------------------------------------------------
# winscard.dll bindings (only the handful of functions we need)
# ---------------------------------------------------------------------------

scard = ctypes.WinDLL("winscard.dll")

SCARD_S_SUCCESS = 0x00000000
SCARD_SCOPE_USER = 0
SCARD_SHARE_SHARED = 2
SCARD_PROTOCOL_T0 = 0x0001
SCARD_PROTOCOL_T1 = 0x0002
SCARD_LEAVE_CARD = 0

SCARD_PCI_T1 = None  # filled in below from the DLL's exported struct


class SCARD_IO_REQUEST(ctypes.Structure):
    _fields_ = [("dwProtocol", wintypes.DWORD), ("cbPciLength", wintypes.DWORD)]


# Locate the exported g_rgSCardT1Pci structure for SCardTransmit's pioSendPci arg
try:
    SCARD_PCI_T1 = SCARD_IO_REQUEST.in_dll(scard, "g_rgSCardT1Pci")
except ValueError:
    # Fallback: build it manually (standard values for T=1 protocol)
    SCARD_PCI_T1 = SCARD_IO_REQUEST(dwProtocol=SCARD_PROTOCOL_T1, cbPciLength=8)


def check(rc, what):
    if rc != SCARD_S_SUCCESS:
        raise RuntimeError(f"{what} failed: 0x{rc & 0xFFFFFFFF:08X}")


def establish_context():
    ctx = wintypes.HANDLE()
    rc = scard.SCardEstablishContext(SCARD_SCOPE_USER, None, None, ctypes.byref(ctx))
    check(rc, "SCardEstablishContext")
    return ctx


def list_readers(ctx):
    size = wintypes.DWORD(0)
    rc = scard.SCardListReadersW(ctx, None, None, ctypes.byref(size))
    check(rc, "SCardListReadersW (size query)")

    buf = ctypes.create_unicode_buffer(size.value)
    rc = scard.SCardListReadersW(ctx, None, buf, ctypes.byref(size))
    check(rc, "SCardListReadersW")

    # Multi-string: names separated by \0, double \0 terminated
    raw = ctypes.wstring_at(buf, size.value)
    names = [n for n in raw.split("\x00") if n]
    return names


def connect(ctx, reader_name, timeout_seconds=15):
    import time
    handle = wintypes.HANDLE()
    active_protocol = wintypes.DWORD()

    deadline = time.time() + timeout_seconds
    last_rc = None
    while time.time() < deadline:
        rc = scard.SCardConnectW(
            ctx,
            reader_name,
            SCARD_SHARE_SHARED,
            SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1,
            ctypes.byref(handle),
            ctypes.byref(active_protocol),
        )
        if rc == SCARD_S_SUCCESS:
            return handle, active_protocol.value
        last_rc = rc
        time.sleep(0.3)

    check(last_rc, "SCardConnectW (timed out waiting for tag - is it placed on the reader?)")


def transmit(handle, apdu_bytes):
    send_buf = (ctypes.c_ubyte * len(apdu_bytes))(*apdu_bytes)
    recv_buf = (ctypes.c_ubyte * 258)()
    recv_len = wintypes.DWORD(len(recv_buf))

    rc = scard.SCardTransmit(
        handle,
        ctypes.byref(SCARD_PCI_T1),
        send_buf,
        len(apdu_bytes),
        None,
        recv_buf,
        ctypes.byref(recv_len),
    )
    check(rc, "SCardTransmit")
    return bytes(recv_buf[: recv_len.value])


def disconnect(handle):
    scard.SCardDisconnect(handle, SCARD_LEAVE_CARD)


def release_context(ctx):
    scard.SCardReleaseContext(ctx)


# ---------------------------------------------------------------------------
# NDEF construction (same format NTAG21x / OpenSpool tags expect)
# ---------------------------------------------------------------------------

def build_ndef_message(json_bytes: bytes) -> bytes:
    """Wrap raw JSON bytes in a single NDEF MIME record (application/json)."""
    mime_type = b"application/json"
    payload = json_bytes

    if len(payload) < 256:
        header = 0xD2  # MB=1 ME=1 SR=1 TNF=2 (MIME media type), single short record
        record = bytes([header, len(mime_type), len(payload)]) + mime_type + payload
    else:
        header = 0xC2
        record = bytes([header, len(mime_type)]) + len(payload).to_bytes(4, "big") + mime_type + payload

    return record


def wrap_tlv(ndef_message: bytes) -> bytes:
    """Wrap an NDEF message in NFC Forum Type 2 Tag TLV format."""
    length = len(ndef_message)
    if length < 255:
        tlv = bytes([0x03, length]) + ndef_message
    else:
        tlv = bytes([0x03, 0xFF]) + length.to_bytes(2, "big") + ndef_message
    tlv += bytes([0xFE])  # Terminator TLV
    return tlv


# ---------------------------------------------------------------------------
# Tag writing
# ---------------------------------------------------------------------------

def write_pages(handle, page_data: bytes, start_page: int = 4):
    if len(page_data) % 4 != 0:
        page_data += b"\x00" * (4 - len(page_data) % 4)

    page = start_page
    for i in range(0, len(page_data), 4):
        chunk = page_data[i:i + 4]
        # ACR122U pseudo-APDU: Write Binary (works for MIFARE Ultralight/NTAG)
        apdu = [0xFF, 0xD6, 0x00, page, 0x04] + list(chunk)
        resp = transmit(handle, apdu)
        sw1, sw2 = resp[-2], resp[-1]
        ok = (sw1 == 0x90 and sw2 == 0x00)
        print(f"  Write page {page}: {'OK' if ok else 'FAIL'} (SW={sw1:02X}{sw2:02X})")
        if not ok:
            raise RuntimeError(f"Write failed at page {page}")
        page += 1

    return page


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print("Usage: python write_tag.py <spool.json>")
        sys.exit(1)

    json_path = sys.argv[1]
    with open(json_path, "rb") as f:
        raw = f.read()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: {json_path} is not valid JSON: {e}")
        sys.exit(1)

    compact_json = json.dumps(parsed, separators=(",", ":")).encode("utf-8")
    print(f"Loaded {json_path}: {len(compact_json)} bytes (compacted)")

    ndef_message = build_ndef_message(compact_json)
    tlv_data = wrap_tlv(ndef_message)
    print(f"NDEF TLV payload: {len(tlv_data)} bytes total")

    if len(tlv_data) > 137:
        print("WARNING: Payload may be too large for NTAG213 (144 bytes user memory).")
        print("  Consider NTAG215 (504 bytes) or NTAG216 (888 bytes) tags for larger JSON.")

    ctx = establish_context()
    try:
        readers = list_readers(ctx)
        if not readers:
            print("ERROR: No PC/SC readers found.")
            print("  - Make sure the ACR122U is plugged in")
            print("  - Check it's using the default CCID driver (Device Manager), not WinUSB")
            print("  - Check Windows 'Smart Card' service is running (services.msc)")
            sys.exit(1)

        if len(readers) > 1:
            print(f"Multiple readers found: {readers}")
            acr_matches = [r for r in readers if "ACR122" in r.upper() or "ACS" in r.upper()]
            if acr_matches:
                reader_name = acr_matches[0]
            else:
                print("WARNING: No reader name contains 'ACR122' or 'ACS'.")
                print("Defaulting to the first reader, but this may be wrong.")
                reader_name = readers[0]
        else:
            reader_name = readers[0]
        print(f"Using reader: {reader_name}")

        print("Place the tag on the reader now (waiting up to 15 seconds)...")
        handle, protocol = connect(ctx, reader_name)
        print(f"Tag connected (protocol={protocol}).")

        try:
            print(f"\nWriting {len(tlv_data)} bytes starting at page 4...")
            write_pages(handle, tlv_data, start_page=4)
            print("\n✅ Done. Tag written successfully.")

            hex_str = parsed.get("color_hex", "")
            try:
                r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
                swatch = f"\033[48;2;{r};{g};{b}m   \033[0m"
            except (ValueError, IndexError):
                swatch = "   "

            subtype = parsed.get("subtype", "") or "(none)"
            print()
            print("  Brand   :", parsed.get("brand", "N/A"))
            print("  Type    :", parsed.get("type", "N/A"))
            print("  Subtype :", subtype)
            print(f"  Color   : {swatch}  #{hex_str}")
            print("  ID      :", parsed.get("spool_id", "N/A"))

            print("\nTip: verify with the printtag-web.pages.dev Read Tag function on your phone,")
            print("or the NFC Tools app.")
        finally:
            disconnect(handle)
    finally:
        release_context(ctx)


if __name__ == "__main__":
    main()