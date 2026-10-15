"""Download one monthly Lichess dump and verify it against the published SHA-256 checksum.

Usage:  python src/download.py
Skips the download if a verified file is already on disk.
"""
import hashlib
import sys
import urllib.request

from config import RAW_DIR, RAW_FILE

BASE_URL = "https://database.lichess.org/standard/"
CHUNK = 1024 * 1024  # 1 MB


def expected_sha256(filename: str) -> str:
    """Look up the official checksum for `filename` from Lichess's sha256sums.txt."""
    with urllib.request.urlopen(BASE_URL + "sha256sums.txt") as resp:
        for line in resp.read().decode().splitlines():
            checksum, name = line.split()
            if name == filename:
                return checksum
    sys.exit(f"No checksum listed for {filename}")


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest) -> None:
    """Stream `url` to disk in 1 MB chunks (never holds the file in memory)."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
        total = int(resp.headers["Content-Length"])
        print(f"Downloading {url}  ({total / 1e9:.2f} GB)")
        done, next_report = 0, 0.0
        while chunk := resp.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            if done / total >= next_report:
                print(f"  {done / total:4.0%}  {done / 1e9:.2f} GB", flush=True)
                next_report += 0.05
    tmp.rename(dest)  # only a complete download gets the final name


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    checksum = expected_sha256(RAW_FILE.name)

    if RAW_FILE.exists() and sha256_of(RAW_FILE) == checksum:
        print(f"Already downloaded and verified: {RAW_FILE.name}")
        return

    download(BASE_URL + RAW_FILE.name, RAW_FILE)
    print("Verifying checksum...")
    if sha256_of(RAW_FILE) != checksum:
        RAW_FILE.unlink()
        sys.exit("Checksum mismatch: file deleted, please re-run.")
    print(f"OK: {RAW_FILE.name} verified ({RAW_FILE.stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
