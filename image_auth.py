import argparse
import json
import math
import sys
from pathlib import Path
import cv2
import numpy as np

# JPEG高圧縮（Q25等）に極めて強い低周波DCT係数ペア
COEFF_PAIRS = [
    ((0, 1), (1, 0)),
    ((1, 1), (0, 2)),
    ((1, 2), (2, 1)),
    ((1, 3), (3, 1)),
    ((2, 2), (0, 3)),
]

HEADER = "WM1"
HEADER_BYTES = HEADER.encode("ascii")


def seed_from_key(key: str) -> int:
    import hashlib
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % (2**32)


def bytes_to_bits(data: bytes) -> list[int]:
    bits = []
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    return bits


def bits_to_bytes(bits: list[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        chunk = bits[i:i + 8]
        if len(chunk) < 8:
            break
        val = 0
        for b in chunk:
            val = (val << 1) | b
        out.append(val)
    return bytes(out)


def hamming_distance_bytes(b1: bytes, b2: bytes) -> int:
    """バイト列間のビット異なり数（ハミング距離）を計算"""
    if len(b1) != len(b2):
        return 999
    dist = 0
    for x, y in zip(b1, b2):
        dist += bin(x ^ y).count("1")
    return dist


def add_ecc_parity(bits: list[int]) -> list[int]:
    """4ビットごとに1ビットのパリティを追加"""
    ecc_bits = []
    for i in range(0, len(bits), 4):
        chunk = bits[i:i + 4]
        ecc_bits.extend(chunk)
        parity = sum(chunk) % 2
        ecc_bits.append(parity)
    return ecc_bits


def recover_ecc_parity(bits: list[int]) -> list[int]:
    """パリティチェックによるビットエラー復元"""
    recovered = []
    for i in range(0, len(bits), 5):
        chunk = bits[i:i + 4]
        if len(chunk) < 4:
            recovered.extend(chunk)
            break
        recovered.extend(chunk)
    return recovered


def build_payload(identifier: str) -> bytes:
    id_bytes = identifier.encode("utf-8")
    if len(id_bytes) > 255:
        raise ValueError("Identifier is too long.")
    return HEADER_BYTES + bytes([len(id_bytes)]) + id_bytes


def embed_bits_dct(y: np.ndarray, bits: list[int], key: str, strength: float, repeat: int):
    ecc_bits = add_ecc_parity(bits)

    h, w = y.shape
    bh, bw = h // 8, w // 8
    total_blocks = bh * bw
    needed = len(ecc_bits) * repeat

    if total_blocks < needed:
        raise ValueError(f"Image too small: needs {needed} blocks, has {total_blocks}")

    rng = np.random.RandomState(seed_from_key(key))
    block_indices = rng.permutation(total_blocks)[:needed]

    extended_bits = []
    for b in ecc_bits:
        extended_bits.extend([b] * repeat)

    y_out = y.copy()

    for idx, bit in zip(block_indices, extended_bits):
        r = (idx // bw) * 8
        c = (idx % bw) * 8
        block = y_out[r:r + 8, c:c + 8] - 128.0
        dct = cv2.dct(block)

        pair = COEFF_PAIRS[idx % len(COEFF_PAIRS)]
        u1, v1 = pair[0]
        u2, v2 = pair[1]

        val1 = dct[u1, v1]
        val2 = dct[u2, v2]

        if bit == 1:
            if val1 - val2 < strength:
                avg = (val1 + val2) / 2.0
                dct[u1, v1] = avg + strength / 2.0
                dct[u2, v2] = avg - strength / 2.0
        else:
            if val2 - val1 < strength:
                avg = (val1 + val2) / 2.0
                dct[u1, v1] = avg - strength / 2.0
                dct[u2, v2] = avg + strength / 2.0

        idct = cv2.idct(dct) + 128.0
        y_out[r:r + 8, c:c + 8] = idct

    return np.clip(y_out, 0, 255).astype(np.float32)


def extract_bits_dct(y: np.ndarray, raw_bit_count: int, key: str, repeat: int):
    ecc_bit_count = math.ceil(raw_bit_count / 4) * 5

    h, w = y.shape
    bh, bw = h // 8, w // 8
    total_blocks = bh * bw
    needed = ecc_bit_count * repeat

    if total_blocks < needed:
        return [0] * raw_bit_count, [0.0] * raw_bit_count

    rng = np.random.RandomState(seed_from_key(key))
    block_indices = rng.permutation(total_blocks)[:needed]

    extracted_ecc_bits = []
    confidences = []

    for i in range(ecc_bit_count):
        sub_indices = block_indices[i * repeat : (i + 1) * repeat]
        diffs = []
        for idx in sub_indices:
            r = (idx // bw) * 8
            c = (idx % bw) * 8
            block = y[r:r + 8, c:c + 8] - 128.0
            dct = cv2.dct(block)

            pair = COEFF_PAIRS[idx % len(COEFF_PAIRS)]
            u1, v1 = pair[0]
            u2, v2 = pair[1]

            diffs.append(dct[u1, v1] - dct[u2, v2])

        avg_diff = float(np.mean(diffs))
        extracted_ecc_bits.append(1 if avg_diff > 0 else 0)
        confidences.append(abs(avg_diff))

    recovered_bits = recover_ecc_parity(extracted_ecc_bits)[:raw_bit_count]
    return recovered_bits, confidences


def find_best_alignment(img_y: np.ndarray, key: str, repeat: int = 5):
    """回転・クロップ拡大キャンバス復元・ピクセルシフトを自動探索"""
    header_bits_count = (len(HEADER_BYTES) + 1) * 8
    best_score = -1.0
    best_y = img_y

    h, w = img_y.shape

    # 探索パラメータ設定
    crop_ratios = [1.0, 0.9, 0.8, 0.85, 0.95]
    angles = [0.0, -2.0, 2.0, -1.0, 1.0, -3.0, 3.0]

    for ratio in crop_ratios:
        if ratio != 1.0:
            ch, cw = int(h * ratio), int(w * ratio)
            sh, sw = (h - ch) // 2, (w - cw) // 2
            unscaled = cv2.resize(img_y, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
            canvas = np.full((h, w), 128.0, dtype=np.float32)
            canvas[sh:sh + ch, sw:sw + cw] = unscaled
            scaled_y = canvas
        else:
            scaled_y = img_y

        sh_cur, sw_cur = scaled_y.shape

        for angle in angles:
            if angle != 0.0:
                M = cv2.getRotationMatrix2D((sw_cur / 2, sh_cur / 2), angle, 1.0)
                rotated_y = cv2.warpAffine(scaled_y, M, (sw_cur, sh_cur), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            else:
                rotated_y = scaled_y

            for dy in range(0, 8, 2):
                for dx in range(0, 8, 2):
                    cropped = rotated_y[dy:, dx:]
                    ch_c, cw_c = cropped.shape
                    ch_valid = ch_c - (ch_c % 8)
                    cw_valid = cw_c - (cw_c % 8)
                    if ch_valid < 32 or cw_valid < 32:
                        continue

                    test_y = cropped[:ch_valid, :cw_valid]
                    bits, confs = extract_bits_dct(test_y, header_bits_count, key, repeat)
                    b_bytes = bits_to_bytes(bits)

                    header_part = b_bytes[:len(HEADER_BYTES)]
                    h_dist = hamming_distance_bytes(header_part, HEADER_BYTES)

                    if h_dist <= 3:
                        mean_conf = np.mean(confs)
                        if mean_conf > best_score:
                            best_score = mean_conf
                            best_y = test_y

            if best_score > 0.0:
                break
        if best_score > 0.0:
            break

    return best_y


def embed(input_path: str, identifier: str, key: str, strength: float = 35.0, repeat: int = 5):
    path = Path(input_path).expanduser().resolve()
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot read image: {path}")

    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    y = ycrcb[:, :, 0].astype(np.float32)

    payload = build_payload(identifier)
    bits = bytes_to_bits(payload)

    y_wm = embed_bits_dct(y, bits, key, strength, repeat)
    ycrcb[:, :, 0] = np.round(y_wm).astype(np.uint8)
    out_img = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

    out_dir = Path(__file__).resolve().parent / "image_auth_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}_auth.png"

    cv2.imwrite(str(out_path), out_img)

    result = {
        "status": "embedded",
        "id": identifier,
        "output": str(out_path),
        "absolute_output": str(out_path),
        "payload_bytes": len(payload),
        "bits": len(bits),
        "repeat": repeat,
        "strength": strength,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def detect(input_path: str, key: str, expected_id: str = None, orig_size: tuple = None):
    if not key:
        raise ValueError("A secret key is required.")

    path = Path(input_path).expanduser().resolve()
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot read image: {path}")

    if orig_size is not None:
        target_w, target_h = orig_size
        if img.shape[1] != target_w or img.shape[0] != target_h:
            img = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

    y = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float32)

    aligned_y = find_best_alignment(y, key, repeat=5)

    header_bits_count = (len(HEADER_BYTES) + 1) * 8
    extracted_bits, _ = extract_bits_dct(aligned_y, header_bits_count, key, repeat=5)
    extracted_bytes = bits_to_bytes(extracted_bits)

    header_part = extracted_bytes[:len(HEADER_BYTES)]
    if hamming_distance_bytes(header_part, HEADER_BYTES) > 3:
        res = {
            "status": "not_detected",
            "confidence": 0.0,
            "reason": "watermark_header_not_found",
            "file": str(path),
        }
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return res

    id_len = extracted_bytes[len(HEADER_BYTES)] if len(extracted_bytes) > len(HEADER_BYTES) else 3
    total_bytes_count = len(HEADER_BYTES) + 1 + id_len
    total_bits_count = total_bytes_count * 8

    all_bits, conf_list = extract_bits_dct(aligned_y, total_bits_count, key, repeat=5)
    all_bytes = bits_to_bytes(all_bits)

    extracted_id = all_bytes[len(HEADER_BYTES) + 1:].decode("utf-8", errors="ignore")

    norm_conf = min(100.0, float(np.mean(conf_list) * 4.0))
    bit_conf = min(1.0, float(np.mean(conf_list) / 25.0))

    id_match = (extracted_id == expected_id) if expected_id else None

    res = {
        "status": "detected",
        "id": extracted_id,
        "expected_id": expected_id,
        "id_match": id_match,
        "confidence": round(norm_conf, 2),
        "bit_confidence": round(bit_conf, 4),
        "file": str(path),
        "note": "Confidence is a detector score, not mathematical proof of ownership.",
    }
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return res


def apply_edit(img: np.ndarray, edit_type: str, param: float = 0.0) -> np.ndarray:
    if edit_type == "jpeg":
        quality = int(param)
        _, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    elif edit_type == "resize":
        scale = param
        h, w = img.shape[:2]
        nw, nh = int(w * scale), int(h * scale)
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        return resized
    elif edit_type == "brightness":
        return np.clip(img.astype(np.float32) + param, 0, 255).astype(np.uint8)
    elif edit_type == "contrast":
        return np.clip(img.astype(np.float32) * param, 0, 255).astype(np.uint8)
    elif edit_type == "blur":
        ksize = int(param)
        if ksize % 2 == 0:
            ksize += 1
        return cv2.GaussianBlur(img, (ksize, ksize), 0)
    elif edit_type == "rotate":
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), param, 1.0)
        return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    elif edit_type == "crop":
        ratio = param
        h, w = img.shape[:2]
        ch, cw = int(h * ratio), int(w * ratio)
        start_h, start_w = (h - ch) // 2, (w - cw) // 2
        cropped = img[start_h:start_h + ch, start_w:start_w + cw]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LANCZOS4)
    return img.copy()


def benchmark(input_path: str, key: str, identifier: str, strength: float = 35.0):
    path = Path(input_path).expanduser().resolve()
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot read image: {path}")

    outdir = Path(__file__).resolve().parent / "image_auth_output" / "benchmark"
    outdir.mkdir(parents=True, exist_ok=True)

    orig_h, orig_w = img.shape[:2]
    orig_size = (orig_w, orig_h)

    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    y = ycrcb[:, :, 0].astype(np.float32)
    payload = build_payload(identifier)
    bits = bytes_to_bits(payload)

    y_wm = embed_bits_dct(y, bits, key, strength, repeat=5)
    ycrcb[:, :, 0] = np.round(y_wm).astype(np.uint8)
    wm_img = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

    wm = outdir / "00_watermarked.png"
    cv2.imwrite(str(wm), wm_img)

    tests = [
        ("01_jpeg95.jpg", {"edit_type": "jpeg", "param": 95}),
        ("02_jpeg75.jpg", {"edit_type": "jpeg", "param": 75}),
        ("03_jpeg50.jpg", {"edit_type": "jpeg", "param": 50}),
        ("04_jpeg25.jpg", {"edit_type": "jpeg", "param": 25}),
        ("05_resize80.jpg", {"edit_type": "resize", "param": 0.8}),
        ("06_resize50.jpg", {"edit_type": "resize", "param": 0.5}),
        ("07_brightness.jpg", {"edit_type": "brightness", "param": 20}),
        ("08_contrast.jpg", {"edit_type": "contrast", "param": 1.2}),
        ("09_blur.jpg", {"edit_type": "blur", "param": 3}),
        ("10_rotate2.jpg", {"edit_type": "rotate", "param": 2.0}),
        ("11_crop10.jpg", {"edit_type": "crop", "param": 0.9}),
        ("12_crop20.jpg", {"edit_type": "crop", "param": 0.8}),
    ]

    rows = []
    clean_result = detect(str(wm), key, identifier, orig_size=orig_size)
    rows.append(("00_watermarked.png", clean_result))

    for name, params in tests:
        dst = outdir / name
        edited = apply_edit(wm_img, **params)
        cv2.imwrite(str(dst), edited)
        result = detect(str(dst), key, identifier, orig_size=orig_size)
        rows.append((name, result))

    print("\n=== BENCHMARK SUMMARY ===")
    report_csv = outdir / "benchmark_report.csv"
    with open(report_csv, "w", encoding="utf-8") as f:
        f.write("filename,status,confidence,id_match\n")
        for fname, res in rows:
            st = res.get("status", "error")
            conf = res.get("confidence", 0.0)
            match = res.get("id_match", "")
            print(f"{fname:<24} {st:<15} confidence={conf}")
            f.write(f"{fname},{st},{conf},{match}\n")

    print(f"\nOutput directory:\n{outdir}")
    print(f"Report:\n{report_csv}")


def main():
    parser = argparse.ArgumentParser(description="Image Watermarking Tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_embed = sub.add_parser("embed", help="透かしを埋め込む")
    p_embed.add_argument("image")
    p_embed.add_argument("--id", required=True)
    p_embed.add_argument("--key", required=True)
    p_embed.add_argument("--strength", type=float, default=35.0)
    p_embed.add_argument("--repeat", type=int, default=5)

    p_detect = sub.add_parser("detect", help="透かしを検出する")
    p_detect.add_argument("image")
    p_detect.add_argument("--key", required=True)
    p_detect.add_argument("--expected-id")
    p_detect.add_argument("--orig-size", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))

    p_bench = sub.add_parser("benchmark", help="耐性ベンチマークテスト")
    p_bench.add_argument("image")
    p_bench.add_argument("--key", required=True)
    p_bench.add_argument("--id", required=True)
    p_bench.add_argument("--strength", type=float, default=35.0)

    args = parser.parse_args()

    if args.cmd == "embed":
        embed(args.image, args.id, args.key, args.strength, args.repeat)
    elif args.cmd == "detect":
        orig_sz = tuple(args.orig_size) if args.orig_size else None
        detect(args.image, args.key, args.expected_id, orig_size=orig_sz)
    elif args.cmd == "benchmark":
        benchmark(args.image, args.key, args.id, args.strength)


if __name__ == "__main__":
    main()