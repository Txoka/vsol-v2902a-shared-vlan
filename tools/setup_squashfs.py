"""Fetch and build a pinned upstream SquashFS toolchain in ignored build/."""
import subprocess
from pathlib import Path
from elf64 import require

REVISION = "db038ef265ed00bb17aaf695a72f9b0c3489eb20"
SOURCE = "https://github.com/plougher/squashfs-tools.git"


def main():
    destination = Path("build/squashfs-source")
    if not destination.exists():
        destination.mkdir(parents=True)
        subprocess.run(["git", "init", str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "remote", "add", "origin", SOURCE], check=True)
        subprocess.run(["git", "-C", str(destination), "fetch", "--depth", "1", "origin", REVISION], check=True)
        subprocess.run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"], check=True)
    revision = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    require(revision == REVISION, "Existing tool checkout has a different revision")
    subprocess.run(["git", "-C", str(destination), "diff", "--exit-code"], check=True)
    subprocess.run(["make", "-C", str(destination / "squashfs-tools"), "-j4", "XZ_SUPPORT=1",
                    "ZSTD_SUPPORT=1", "LZ4_SUPPORT=1", "LZO_SUPPORT=0"], check=True)


if __name__ == "__main__":
    main()
