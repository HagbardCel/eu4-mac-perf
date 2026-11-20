"""Exact four-channel comparison for compressed-904 (uses repo comparator)."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from automation.m6_compare import compare_images  # noqa: E402

EV = Path(__file__).resolve().parent
W, H = 1728, 1117
LIVE_BLOBS = ROOT / ".local/m7/goal-round1/capture-c/frame/blobs"
LIVE = {
    15: LIVE_BLOBS / "default-checkpoint-3f71265b4e0c7afe9d92213bc679cd32c7625f39dc94a5ea1778abce3eae2232.bin",
    16: LIVE_BLOBS / "default-checkpoint-698e928b92bcb915e37366ca507f4a60bec8150e8ffada8d7200bbc1e046a671.bin",
    17: LIVE_BLOBS / "default-checkpoint-7776e6aadd1324da763a48c3c7722de4cb0905ad46202ca6400f176bab01762a.bin",
    "final": None,
}
COMPRESSED = EV / "replay-compressed"
DECODED = EV / "replay-decoded"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def produced(root, event):
    name = "metal-frame.bgra" if event == "final" else f"intermediate-event{event}-tex0.bgra"
    return root / name


def live(event):
    if event == "final":
        return ROOT / ".local/m7/goal-round1/capture-c/frame/frame-reference.bgra"
    return LIVE[event]


def run(reference, candidate, label):
    a = reference.read_bytes()
    b = candidate.read_bytes()
    stats = compare_images(a, b, W, H)
    return {
        "label": label,
        "reference_file": str(reference),
        "reference_sha256": hashlib.sha256(a).hexdigest(),
        "candidate_file": str(candidate),
        "candidate_sha256": hashlib.sha256(b).hexdigest(),
        "identical": a == b,
        "statistics": stats,
    }


def main():
    out = {
        "schema": "compressed-904/pixel-comparison/v1",
        "comparator": "automation.m6_compare.compare_images",
        "width": W, "height": H,
        "produced_digests": {},
        "pairs": [],
    }
    for event in (15, 16, 17, "final"):
        c = produced(COMPRESSED, event)
        d = produced(DECODED, event)
        out["produced_digests"][f"compressed:{event}"] = {
            "file": str(c), "sha256": sha(c), "bytes": c.stat().st_size}
        out["produced_digests"][f"decoded:{event}"] = {
            "file": str(d), "sha256": sha(d), "bytes": d.stat().st_size}
        out["pairs"].append(run(c, d, f"i: compressed vs decoded event {event}"))
        out["pairs"].append(run(live(event), c, f"ii: compressed vs live-GL seal event {event}"))
        out["pairs"].append(run(live(event), d, f"iii: decoded vs live-GL seal event {event}"))
    # sanity: our compressed run vs the pre-existing shadow outputs
    shadow = ROOT / ".local/m7/goal-round1/capture-c/shadow"
    out["shadow_sanity"] = {}
    for event in (15, 16, 17, "final"):
        name = "metal-frame.bgra" if event == "final" else f"intermediate-event{event}-tex0.bgra"
        a = shadow / name
        b = produced(COMPRESSED, event)
        out["shadow_sanity"][str(event)] = {
            "existing_shadow": str(a), "existing_shadow_sha256": sha(a),
            "compressed_replay_sha256": sha(b),
            "byte_identical": a.read_bytes() == b.read_bytes(),
        }
    path = EV / "pixel-comparison.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for pair in out["pairs"]:
        s = pair["statistics"]
        print(f"{pair['label']}: mismatched={s['mismatched_pixels']} "
              f"max={s['max_channel_error']} alpha={s['alpha_different_pixels']} "
              f"identical={pair['identical']}")
    print("shadow sanity:", json.dumps(out["shadow_sanity"], indent=1))
    print("wrote", path)


if __name__ == "__main__":
    main()
