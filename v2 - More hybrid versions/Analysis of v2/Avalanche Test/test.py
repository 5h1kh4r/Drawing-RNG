import argparse
import json
import random
from pathlib import Path
import numpy as np
from PIL import Image

from main import sha256_image, v1_rng, hybrid_v2


def bit_hamming_distance(a: bytes, b: bytes) -> int:
    return sum((x ^ y).bit_count() for x, y in zip(a, b))


def flip_pixel(img: np.ndarray, x: int, y: int):
    modified = img.copy()
    modified[y, x] ^= 0xFF
    return modified


def save_img(arr, path):
    Image.fromarray(arr.astype(np.uint8)).save(path)


def get_output(mode, path, num_bytes, warmup):
    if mode == "sha256":
        return sha256_image(path, num_bytes)
    elif mode == "v1":
        return v1_rng(path, num_bytes, warmup)
    elif mode == "v2":
        return hybrid_v2(path, num_bytes, warmup)
    else:
        raise ValueError("Invalid mode")


def avalanche_test(image_path, mode, trials, num_bytes, warmup, tmp_dir):
    base_img = np.array(Image.open(image_path).convert("L"), dtype=np.uint8)
    h, w = base_img.shape

    base_out = get_output(mode, image_path, num_bytes, warmup)

    total_bits = num_bytes * 8
    ratios = []

    for i in range(trials):
        x = random.randint(0, w - 1)
        y = random.randint(0, h - 1)

        modified = flip_pixel(base_img, x, y)
        tmp_path = tmp_dir / f"tmp_{mode}_{i}.png"
        save_img(modified, tmp_path)

        new_out = get_output(mode, tmp_path, num_bytes, warmup)

        hd = bit_hamming_distance(base_out, new_out)
        ratios.append(hd / total_bits)

    return {
        "image": image_path.stem,
        "mode": mode,
        "mean": round(float(np.mean(ratios)), 6),
        "std": round(float(np.std(ratios)), 6),
        "min": round(float(np.min(ratios)), 6),
        "max": round(float(np.max(ratios)), 6),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-dir", default=Path("Images"), type=Path)
    parser.add_argument("--mode", choices=["v1", "v2", "sha256"], default="v2")
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--num-bytes", type=int, default=4096)
    parser.add_argument("--warmup", type=int, default=256)
    parser.add_argument("--out", type=Path, default=Path("avalanche_compare.json"))

    args = parser.parse_args()

    images = list(args.image_dir.glob("*"))
    tmp_dir = Path("tmp")
    tmp_dir.mkdir(exist_ok=True)

    results = []

    for img in images:
        res = avalanche_test(
            img,
            args.mode,
            args.trials,
            args.num_bytes,
            args.warmup,
            tmp_dir
        )
        print(res)
        results.append(res)

    args.out.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()