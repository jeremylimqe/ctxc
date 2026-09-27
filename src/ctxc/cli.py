"""Minimal filesystem-to-context compiler; experimental v0.0.1."""
from __future__ import annotations

import argparse
import tomllib
import unicodedata
from pathlib import Path


def load_named_config(path: Path, name: str) -> dict:
    """Load one named configuration. Input/output paths remain CWD-relative."""
    with path.open("rb") as file:
        return tomllib.load(file)["configs"][name]


def _has_symlink(path: Path) -> bool:
    return any(part.is_symlink() for part in (path, *path.parents))


def files_under(directory: Path, suffixes: set[str]) -> list[Path]:
    """Recursively select files, skipping symlinked paths."""
    if _has_symlink(directory):
        return []
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    files: list[Path] = []
    for path in directory.iterdir():
        if _has_symlink(path):
            continue
        if path.is_dir():
            files.extend(files_under(path, suffixes))
        elif path.is_file() and path.suffix.lower() in suffixes:
            files.append(path)
    return files  # One canonical sort happens after all selections are combined.


def normalize_suffixes(extensions: list[str]) -> set[str]:
    """Normalize extensions to lowercase suffixes with leading dots."""
    return {
        (extension if extension.startswith(".") else f".{extension}").lower()
        for extension in extensions
    }


def _serialize_block(block_id: str, data: bytes) -> bytes:
    if (
        not block_id
        or block_id != block_id.strip()
        or len(block_id.splitlines()) != 1
        or any(unicodedata.category(char) == "Cc" for char in block_id)
    ):
        raise ValueError(f"Invalid block identifier: {block_id!r}")
    identifier = block_id.encode("utf-8", errors="strict")
    data.decode("utf-8", errors="strict")
    closing = b"@@ctx.end " + identifier
    if closing in data.split(b"\n"):
        raise ValueError(f"Closing delimiter collision in {block_id!r}")
    # The extra LF before closing is framing, not part of the payload.
    return b"@@ctx " + identifier + b"\n" + data + b"\n" + closing + b"\n"


def build_code_bundle(config: dict) -> None:
    """Compile configured files into out_code. The current directory is the root.

    Overlapping selections of the same canonical file are included once.
    All inputs are read and validated before opening the output for writing.
    Output replacement is not atomic in this initial release.
    """
    out_code = Path(config["out_code"])
    if not config.get("extensions"):
        raise ValueError("Config 'extensions' must contain at least one extension.")
    suffixes = normalize_suffixes(config["extensions"])
    root = Path.cwd().resolve()
    out_resolved = out_code.resolve()
    if _has_symlink(out_code):
        raise ValueError("Output must not be a symlink or use a symlinked parent.")

    files: list[Path] = []
    for directory in config.get("dirs", []):
        files.extend(files_under(Path(directory), suffixes))
    for file in config.get("files", []):
        path = Path(file)
        if _has_symlink(path):
            continue
        if not path.is_file():
            raise FileNotFoundError(f"Not a file: {path}")
        files.append(path)

    by_id: dict[str, Path] = {}
    for file in files:
        canonical = file.resolve(strict=True)
        if canonical == out_resolved:
            continue
        if not canonical.is_relative_to(root):
            raise ValueError(f"Input is outside the current-directory root: {file}")
        # Different spellings of the same path cannot emit duplicate block IDs.
        by_id[canonical.relative_to(root).as_posix()] = canonical

    artifact = b"".join(
        _serialize_block(block_id, by_id[block_id].read_bytes())
        for block_id in sorted(by_id, key=lambda value: value.encode("utf-8"))
    )
    out_code.parent.mkdir(parents=True, exist_ok=True)
    out_code.write_bytes(artifact)
    print(f"Generated {out_code} from {len(by_id)} file(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        build_code_bundle(load_named_config(args.config, args.name))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"ctxc: {error}\n")


if __name__ == "__main__":
    main()
