#!/usr/bin/env python
"""
Download the pre-formatted Xenium benchmarking AnnData files from Zenodo.

The 21 datasets are split across four Zenodo records. This script queries the
Zenodo REST API for each record, builds a filename -> download-URL map, and
fetches the requested files with resume support and MD5 verification.

Usage:
    python download_xenium_adata.py --dry-run     # show manifest + total size
    python download_xenium_adata.py               # download everything
    python download_xenium_adata.py -f ms_brain_multisection1.h5ad
"""

import argparse
import hashlib
import os
import sys

import requests

# Zenodo records holding the AnnData conversions (Marco Salas et al., 2024)
RECORDS = {
    "11120307": "part 1/4 - mouse brain",
    "11121221": "part 2/4 - human breast",
    "11124988": "part 3/4 - miscellaneous",
    "11120922": "spinal cord (end-to-end pipeline example)",
}

FILES = [
    "ms_brain_multisection1.h5ad",
    "human_brain.h5ad",
    "ms_brain_multisection2.h5ad",
    "ms_brain_multisection3.h5ad",
    "realmouse_1.h5ad",
    "realmouse_2.h5ad",
    "realmouse_3.h5ad",
    "realmouse_4.h5ad",
    "hbreast_ilc_addon_set2.h5ad",
    "hbreast_ilc_addon_set4.h5ad",
    "hbreast_ilc_entiresample_set3.h5ad",
    "healthy_lung.h5ad",
    "human_alzheimers.h5ad",
    "human_gbm.h5ad",
    "human_spinal_chord_active.h5ad",
    "human_spinal_chord_inactive.h5ad",
    "h_breast_1.h5ad",
    "h_breast_2.h5ad",
    "lung_cancer.h5ad",
    "ms_brain_fullcoronal.h5ad",
    "ms_brain_partialcoronal.h5ad",
]

OUTPUT_DIR = "../../data/unprocessed_adata_nuclei/"
CHUNK = 8 * 1024 * 1024  # 8 MB


def human(nbytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return "{:.1f} {}".format(nbytes, unit)
        nbytes /= 1024.0


def build_manifest():
    """Query each Zenodo record and map filename -> (url, size, md5, record)."""
    manifest = {}
    for rec_id, label in RECORDS.items():
        url = "https://zenodo.org/api/records/{}".format(rec_id)
        print("Querying record {} ({}) ...".format(rec_id, label))
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            print("  WARNING: could not query {}: {}".format(rec_id, exc))
            continue

        payload = r.json()
        entries = payload.get("files", [])
        # Zenodo's legacy API returns a list; InvenioRDM may nest under "entries".
        if isinstance(entries, dict):
            entries = list(entries.get("entries", {}).values())

        for entry in entries:
            key = entry.get("key") or entry.get("filename")
            if not key:
                continue
            checksum = entry.get("checksum", "") or ""
            if checksum.startswith("md5:"):
                checksum = checksum[4:]
            manifest[key] = {
                "url": "https://zenodo.org/records/{}/files/{}?download=1".format(
                    rec_id, key
                ),
                "size": entry.get("size", 0),
                "md5": checksum,
                "record": rec_id,
            }
        print("  found {} files".format(len(entries)))
    return manifest


def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name, meta, outdir, verify=True):
    dest = os.path.join(outdir, name)
    expected = meta["size"]

    if os.path.exists(dest) and expected and os.path.getsize(dest) == expected:
        print("  {} already complete, skipping".format(name))
        return True

    # Resume from wherever the partial file left off.
    pos = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"Range": "bytes={}-".format(pos)} if pos else {}
    mode = "ab" if pos else "wb"
    if pos:
        print("  resuming {} at {}".format(name, human(pos)))

    try:
        r = requests.get(meta["url"], headers=headers, stream=True, timeout=120)
        r.raise_for_status()
    except requests.RequestException as exc:
        print("  ERROR downloading {}: {}".format(name, exc))
        return False

    done = pos
    with open(dest, mode) as fh:
        for chunk in r.iter_content(chunk_size=CHUNK):
            if not chunk:
                continue
            fh.write(chunk)
            done += len(chunk)
            if expected:
                pct = 100.0 * done / expected
                sys.stdout.write(
                    "\r  {}: {} / {} ({:.1f}%)".format(
                        name, human(done), human(expected), pct
                    )
                )
                sys.stdout.flush()
    sys.stdout.write("\n")

    if verify and meta["md5"]:
        print("  verifying checksum ...")
        actual = md5sum(dest)
        if actual != meta["md5"]:
            print("  CHECKSUM MISMATCH for {}".format(name))
            print("    expected {}".format(meta["md5"]))
            print("    got      {}".format(actual))
            return False
        print("  checksum ok")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output-dir", default=OUTPUT_DIR)
    ap.add_argument(
        "-f",
        "--file",
        action="append",
        dest="files",
        help="download only this file (repeatable); defaults to all 21",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve URLs and report sizes without downloading",
    )
    ap.add_argument(
        "--no-verify",
        action="store_true",
        help="skip MD5 verification (faster, less safe)",
    )
    args = ap.parse_args()

    wanted = args.files if args.files else FILES
    manifest = build_manifest()

    resolved, missing = {}, []
    for name in wanted:
        if name in manifest:
            resolved[name] = manifest[name]
        else:
            missing.append(name)

    total = sum(m["size"] for m in resolved.values())
    print("\nResolved {}/{} files, {} total".format(
        len(resolved), len(wanted), human(total)))

    if missing:
        print("\nNOT FOUND in any of the four records:")
        for name in missing:
            print("  {}".format(name))
        print("These may live in another Zenodo record, or the filename may")
        print("differ from the one used in the notebooks.")

    if args.dry_run:
        print("\nManifest:")
        for name, meta in sorted(resolved.items()):
            print("  {:40s} {:>10s}  record {}".format(
                name, human(meta["size"]), meta["record"]))
        return 0

    outdir = os.path.abspath(args.output_dir)
    os.makedirs(outdir, exist_ok=True)
    print("\nDownloading to {}\n".format(outdir))

    failed = []
    for i, (name, meta) in enumerate(sorted(resolved.items()), 1):
        print("[{}/{}] {}".format(i, len(resolved), name))
        if not download(name, meta, outdir, verify=not args.no_verify):
            failed.append(name)

    if failed:
        print("\nFailed ({}): {}".format(len(failed), ", ".join(failed)))
        print("Re-run the script to resume these.")
        return 1
    print("\nAll files downloaded and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
