"""Create a clean source upload from an allowlist, never workspace data."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ".gitignore", ".gitattributes", "README.md", "THIRD_PARTY_NOTICES.md",
    "pyproject.toml", "MANIFEST.in", "requirements.txt", "Setup.cmd",
    "Start Electrode Monitor.cmd", "electrode_monitor.py", "serial_protocol.py",
    "session_log.py", ".github/workflows/ci.yml", "docs/PROTOCOL.md",
    "docs/GITHUB.md", "tests/test_protocol.py", "tests/test_worker.py",
    "tests/test_logging.py", "tests/hardware_check.py", "tools/package_source.py",
)


def main():
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Required source files missing: {missing}")
    output = ROOT / "release" / "electrode-monitor-source.zip"
    output.parent.mkdir(exist_ok=True)
    names = list(FILES)
    if (ROOT / "LICENSE").is_file():
        names.append("LICENSE")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            archive.write(ROOT / name, "electrode-monitor/" + name)
    print(output)
    print(f"{len(names)} source files; no local data, dependencies or caches")


if __name__ == "__main__":
    main()
