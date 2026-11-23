"""Image checks, conversion, provenance stamps and perceptual hashes."""
from __future__ import annotations

import base64
import io
import json
import struct
import zlib
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 120_000_000

MIN_SIDE = 150
STAMP_PREFIX = b"PROGRAMARR "
# Files written by Litfass, the poster app Programarr grew out of, carry this prefix.
STAMP_PREFIXES = (STAMP_PREFIX, b"LITFASS ")
PNG_KEYWORDS = (b"programarr\x00", b"litfass\x00")


class ImageError(ValueError):
    """The data is not a usable poster image; the message is for the UI."""


@dataclass
class Prepared:
    data: bytes
    ext: str
    width: int
    height: int
    fmt: str


def _open(data: bytes) -> Image.Image:
    if not data:
        raise ImageError("Empty file")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageError("Not a readable image") from exc
    return img


def inspect(data: bytes) -> tuple[str, int, int]:
    img = _open(data)
    return (img.format or "").upper(), img.width, img.height


def prepare(data: bytes, keep_png: bool = True) -> Prepared:
    """Keep JPEG (and PNG if wanted) byte for byte; convert everything else to JPEG."""
    img = _open(data)
    fmt = (img.format or "").upper()
    if img.width < MIN_SIDE or img.height < MIN_SIDE:
        raise ImageError(f"Image too small ({img.width} × {img.height} px)")
    orientation = 1
    try:
        orientation = img.getexif().get(0x0112, 1)
    except Exception:  # pragma: no cover - broken EXIF is treated as upright
        orientation = 1
    if fmt == "JPEG" and img.mode in ("RGB", "L") and orientation == 1:
        return Prepared(data, ".jpg", img.width, img.height, "JPEG")
    if fmt == "PNG" and keep_png and orientation == 1 and img.mode in ("RGB", "RGBA", "L", "LA", "P"):
        return Prepared(data, ".png", img.width, img.height, "PNG")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (0, 0, 0))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95, optimize=True)
    return Prepared(out.getvalue(), ".jpg", img.width, img.height, "JPEG")


# -- provenance stamp ------------------------------------------------------
def _payload(info: dict) -> bytes:
    return STAMP_PREFIX + json.dumps(info, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def stamp(data: bytes, ext: str, info: dict) -> bytes:
    """Embed where a poster came from (and when it was written) in the file itself.

    The stamp is a JPEG comment segment or a PNG iTXt chunk. It also makes every
    written file unique, so Plex treats a re-applied image as a new local asset.
    """
    payload = _payload(info)
    data = strip_stamp(data)
    if ext == ".jpg":
        return _stamp_jpeg(data, payload)
    if ext == ".png":
        return _stamp_png(data, payload)
    return data


def strip_stamp(data: bytes) -> bytes:
    """Remove earlier stamps so restored files do not collect them."""
    try:
        if data[:2] == b"\xff\xd8":
            out, pos = bytearray(data[:2]), 2
            while pos + 4 <= len(data) and data[pos] == 0xFF and data[pos + 1] not in (0xDA, 0xD9):
                length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
                segment = data[pos : pos + 2 + length]
                if not (data[pos + 1] == 0xFE and segment[4:].startswith(STAMP_PREFIXES)):
                    out += segment
                pos += 2 + length
            out += data[pos:]
            return bytes(out)
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            out, pos = bytearray(data[:8]), 8
            while pos + 8 <= len(data):
                length = struct.unpack(">I", data[pos : pos + 4])[0]
                chunk = data[pos : pos + 12 + length]
                if not (chunk[4:8] == b"iTXt" and chunk[8:].startswith(PNG_KEYWORDS)):
                    out += chunk
                pos += 12 + length
            return bytes(out)
    except struct.error:
        return data
    return data


def _stamp_jpeg(data: bytes, payload: bytes) -> bytes:
    if data[:2] != b"\xff\xd8":
        return data
    payload = payload[:65000]
    pos = 2
    # Keep APPn segments (JFIF/EXIF) first, as the JFIF standard expects.
    while pos + 4 <= len(data) and data[pos] == 0xFF and 0xE0 <= data[pos + 1] <= 0xEF:
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        pos += 2 + length
    segment = b"\xff\xfe" + struct.pack(">H", len(payload) + 2) + payload
    return data[:pos] + segment + data[pos:]


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _stamp_png(data: bytes, payload: bytes) -> bytes:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 33:
        return data
    ihdr_len = struct.unpack(">I", data[8:12])[0]
    insert_at = 8 + 12 + ihdr_len
    body = b"programarr\x00\x00\x00\x00\x00" + payload
    return data[:insert_at] + _png_chunk(b"iTXt", body) + data[insert_at:]


def read_stamp(data: bytes) -> dict | None:
    try:
        if data[:2] == b"\xff\xd8":
            return _read_jpeg_stamp(data)
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return _read_png_stamp(data)
    except (struct.error, ValueError, UnicodeDecodeError):
        return None
    return None


def _decode(payload: bytes) -> dict | None:
    prefix = next((p for p in STAMP_PREFIXES if payload.startswith(p)), None)
    if prefix is None:
        return None
    try:
        value = json.loads(payload[len(prefix) :].decode("utf-8"))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _read_jpeg_stamp(data: bytes) -> dict | None:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker in (0xDA, 0xD9):  # start of scan / end of image
            return None
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if marker == 0xFE:
            found = _decode(data[pos + 4 : pos + 2 + length])
            if found is not None:
                return found
        pos += 2 + length
    return None


def _read_png_stamp(data: bytes) -> dict | None:
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        if kind == b"IDAT":
            return None
        if kind == b"iTXt":
            body = data[pos + 8 : pos + 8 + length]
            keyword = next((k for k in PNG_KEYWORDS if body.startswith(k)), None)
            if keyword:
                # keyword \0, compression flag, method, language \0, translated keyword \0
                rest = body[len(keyword) + 2 :]
                rest = rest.split(b"\x00", 2)
                if len(rest) == 3:
                    return _decode(rest[2])
        pos += 12 + length
    return None


def read_stamp_file(path) -> dict | None:
    try:
        with open(path, "rb") as handle:
            head = handle.read(512 * 1024)
    except OSError:
        return None
    return read_stamp(head)


# -- perceptual hashes -----------------------------------------------------
def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n).reshape(-1, 1)
    i = np.arange(n).reshape(1, -1)
    matrix = np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    matrix[0, :] *= 1 / np.sqrt(2)
    return matrix * np.sqrt(2 / n)


_DCT32 = _dct_matrix(32)


def _bits_to_hex(bits: np.ndarray) -> str:
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"


def hashes(data: bytes) -> dict:
    """pHash and dHash (64 bit each) plus a small grey signature for fine checks."""
    img = ImageOps.exif_transpose(_open(data)).convert("L")
    small = np.asarray(img.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    dct = _DCT32 @ small @ _DCT32.T
    low = dct[:8, :8]
    median = np.median(low.flatten()[1:])
    p_bits = low > median
    d_img = np.asarray(img.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.float64)
    d_bits = d_img[:, 1:] > d_img[:, :-1]
    grey = np.asarray(img.resize((12, 18), Image.Resampling.LANCZOS), dtype=np.float32).flatten()
    return {
        "p": _bits_to_hex(p_bits),
        "d": _bits_to_hex(d_bits),
        "g": base64.b64encode(grey.astype(np.uint8).tobytes()).decode("ascii"),
    }


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _grey(value: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(value), dtype=np.uint8).astype(np.float64)


def compare(a: dict, b: dict) -> dict:
    """Distances between two hash sets and a 0–1 similarity score."""
    p = _hamming(a["p"], b["p"])
    d = _hamming(a["d"], b["d"])
    ga, gb = _grey(a["g"]), _grey(b["g"])
    sa, sb = ga.std(), gb.std()
    if sa < 1e-6 or sb < 1e-6:
        corr = 1.0 if abs(ga.mean() - gb.mean()) < 8 else 0.0
    else:
        corr = float(np.mean((ga - ga.mean()) * (gb - gb.mean())) / (sa * sb))
    score = max(0.0, 1 - (p / 64) * 0.9 - (d / 64) * 0.6) * 0.6 + max(0.0, corr) * 0.4
    match = p <= 12 and d <= 14 and corr >= 0.8
    return {"p": p, "d": d, "corr": round(corr, 3), "score": round(min(score, 1.0), 3), "match": match}


def thumbnail(data: bytes, width: int = 360) -> bytes:
    img = ImageOps.exif_transpose(_open(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (0, 0, 0))
        background.paste(img, mask=img.split()[-1])
        img = background
    if img.width > width:
        height = round(img.height * width / img.width)
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()
