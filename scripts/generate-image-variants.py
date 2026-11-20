#!/usr/bin/env python3
"""Generate metadata-free WebP thumbnail and medium variants without cropping or upscaling."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "yademan-images-webp95" / "images"
THUMB = REPO / "yademan-thumbnails-webp"
MEDIUM = REPO / "yademan-medium-webp"
MANIFEST = REPO / "yademan-image-variants.json"
TMP_THUMB = REPO / ".yademan-thumbnails-webp.tmp"
TMP_MEDIUM = REPO / ".yademan-medium-webp.tmp"


def encode(source: Path, destination: Path, bounds: tuple[int, int], quality: int):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        if getattr(opened, "n_frames", 1) != 1:
            raise ValueError("animated source is not supported by this static-image pipeline")
        image = ImageOps.exif_transpose(opened)
        image.load()
        width, height = image.size
        has_alpha = "A" in image.getbands() or "transparency" in opened.info
        if has_alpha:
            image = image.convert("RGBA")
        elif image.mode == "L":
            image = image.convert("L")
        else:
            image = image.convert("RGB")
        image.thumbnail(bounds, Image.Resampling.LANCZOS, reducing_gap=3.0)
        out_width, out_height = image.size
        if out_width > width or out_height > height:
            raise ValueError("upscaling detected")
        image.save(destination, format="WEBP", quality=quality, method=4)
        return width, height, out_width, out_height


def main():
    started = time.time()
    if not SOURCE.is_dir():
        raise SystemExit(f"Source directory not found: {SOURCE}")
    files = sorted(path for path in SOURCE.rglob("*.webp") if path.is_file())
    if len(files) != 3570:
        raise SystemExit(f"Expected 3570 source images, found {len(files)}")

    for path in (TMP_THUMB, TMP_MEDIUM):
        if path.exists():
            shutil.rmtree(path)
    for path in (THUMB, MEDIUM):
        if path.exists():
            shutil.rmtree(path)

    images = []
    source_bytes = thumbnail_bytes = medium_bytes = 0
    errors = []
    for index, source in enumerate(files, 1):
        relative = source.relative_to(SOURCE)
        thumb_path = TMP_THUMB / "images" / relative
        medium_path = TMP_MEDIUM / "images" / relative
        try:
            with Image.open(source) as opened:
                if getattr(opened, "n_frames", 1) != 1:
                    raise ValueError("animated source is not supported")
                oriented = ImageOps.exif_transpose(opened)
                oriented.load()
                source_width, source_height = oriented.size
            tw, th, t_width, t_height = encode(source, thumb_path, (160, 200), 76)
            mw, mh, m_width, m_height = encode(source, medium_path, (480, 600), 82)
            if (tw, th) != (mw, mh):
                raise ValueError("orientation/dimension inconsistency between variants")
            sb = source.stat().st_size
            tb = thumb_path.stat().st_size
            mb = medium_path.stat().st_size
            source_bytes += sb
            thumbnail_bytes += tb
            medium_bytes += mb
            relative_text = relative.as_posix()
            images.append({
                "id": hashlib.sha256(relative_text.encode("utf-8")).hexdigest(),
                "source": f"yademan-images-webp95/images/{relative_text}",
                "thumbnail": f"yademan-thumbnails-webp/images/{relative_text}",
                "medium": f"yademan-medium-webp/images/{relative_text}",
                "source_width": source_width,
                "source_height": source_height,
                "thumbnail_width": t_width,
                "thumbnail_height": t_height,
                "medium_width": m_width,
                "medium_height": m_height,
                "source_bytes": sb,
                "thumbnail_bytes": tb,
                "medium_bytes": mb,
            })
        except Exception as exc:
            errors.append({"source": source.relative_to(REPO).as_posix(), "error": repr(exc)})
        if index % 250 == 0:
            print(f"processed {index}/{len(files)}", flush=True)

    if errors:
        raise RuntimeError(json.dumps({"failed": len(errors), "errors": errors[:20]}, ensure_ascii=False))

    validation = {
        "source_files": len(files),
        "thumbnail_files": 0,
        "medium_files": 0,
        "corrupt_outputs": [],
        "oversized_outputs": [],
        "metadata_outputs": [],
        "path_mismatches": [],
    }
    expected = sorted(path.relative_to(SOURCE).as_posix() for path in files)
    for output_root, bounds, key in (
        (TMP_THUMB, (160, 200), "thumbnail"),
        (TMP_MEDIUM, (480, 600), "medium"),
    ):
        image_root = output_root / "images"
        outputs = sorted(path for path in image_root.rglob("*.webp") if path.is_file())
        actual = [path.relative_to(image_root).as_posix() for path in outputs]
        validation[f"{key}_files"] = len(outputs)
        if actual != expected:
            validation["path_mismatches"].append(key)
        for path in outputs:
            try:
                with Image.open(path) as image:
                    image.load()
                    if image.format != "WEBP":
                        validation["corrupt_outputs"].append(path.relative_to(REPO).as_posix())
                    if image.width > bounds[0] or image.height > bounds[1]:
                        validation["oversized_outputs"].append(path.relative_to(REPO).as_posix())
                    if image.getexif() or any(k in image.info for k in ("exif", "icc_profile", "xmp")):
                        validation["metadata_outputs"].append(path.relative_to(REPO).as_posix())
            except Exception as exc:
                validation["corrupt_outputs"].append({
                    "path": path.relative_to(REPO).as_posix(),
                    "error": repr(exc),
                })

    if (
        validation["thumbnail_files"] != len(files)
        or validation["medium_files"] != len(files)
        or validation["corrupt_outputs"]
        or validation["oversized_outputs"]
        or validation["metadata_outputs"]
        or validation["path_mismatches"]
    ):
        raise RuntimeError("Output validation failed: " + json.dumps(validation, ensure_ascii=False))

    TMP_THUMB.rename(THUMB)
    TMP_MEDIUM.rename(MEDIUM)
    manifest = {
        "version": 1,
        "source_root": "yademan-images-webp95/images",
        "thumbnail_root": "yademan-thumbnails-webp/images",
        "medium_root": "yademan-medium-webp/images",
        "variants": {
            "thumbnail": {
                "format": "WEBP", "max_width": 160, "max_height": 200,
                "quality": 76, "upscale": False, "crop": False,
            },
            "medium": {
                "format": "WEBP", "max_width": 480, "max_height": 600,
                "quality": 82, "upscale": False, "crop": False,
            },
        },
        "image_count": len(images),
        "totals": {
            "source_bytes": source_bytes,
            "thumbnail_bytes": thumbnail_bytes,
            "medium_bytes": medium_bytes,
            "thumbnail_savings_bytes": source_bytes - thumbnail_bytes,
            "medium_savings_bytes": source_bytes - medium_bytes,
            "thumbnail_savings_percent": round((source_bytes - thumbnail_bytes) * 100 / source_bytes, 2),
            "medium_savings_percent": round((source_bytes - medium_bytes) * 100 / source_bytes, 2),
        },
        "validation": validation,
        "elapsed_seconds": round(time.time() - started, 2),
        "images": images,
    }
    temporary_manifest = MANIFEST.with_suffix(".json.tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(MANIFEST)
    print(json.dumps({
        "image_count": len(images),
        "totals": manifest["totals"],
        "validation": validation,
        "elapsed_seconds": manifest["elapsed_seconds"],
        "manifest_bytes": MANIFEST.stat().st_size,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
