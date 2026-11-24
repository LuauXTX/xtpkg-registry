"""Validate and merge registry submissions without executing package code.

Python standard library only. Versions are immutable; generated JSON is UTF-8,
sorted, two-space indented, and newline terminated. Metadata validation is not
source-code review or proof of repository ownership.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile

MAX_BYTES = 2 * 1024 * 1024
NUMBER = r"(?:0|[1-9][0-9]*)"
VERSION = re.compile(rf"^({NUMBER})\.({NUMBER})\.({NUMBER})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/-]+$")
OFFICIAL = {"https://github.com/MINTILER-DEV/xtpkg", "https://github.com/LuauXTX/xtpkg"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def name(value):
    require(isinstance(value, str) and len(value) <= 120, "invalid package name")
    parts = value.split("/")
    require(1 <= len(parts) <= 2, "invalid package scope")
    for part in parts:
        require(len(part) <= 60 and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", part), "invalid package name")
        require(part not in {"con", "prn", "aux", "nul"} and not re.fullmatch(r"(?:com|lpt)[0-9]", part), "reserved device name")
    return value


def version(value):
    require(isinstance(value, str) and len(value) <= 256, "invalid semantic version")
    match = VERSION.fullmatch(value)
    require(match is not None, "invalid semantic version: " + repr(value))
    if match[4]:
        for item in match[4].split("."):
            require(not item.isdigit() or len(item) == 1 or item[0] != "0", "leading zero in prerelease")


def version_range(value):
    require(isinstance(value, str) and 0 < len(value) <= 512 and all(32 <= ord(c) < 127 for c in value), "invalid dependency range")
    tokens = value.split()
    require(tokens, "empty dependency range")
    for token in tokens:
        if token in {"*", "x", "X"} or re.fullmatch(rf"{NUMBER}\.{NUMBER}\.[xX*]", token):
            continue
        match = re.fullmatch(r"(?:\^|~|>=|<=|>|<|=)?(.+)", token)
        version(match[1])


def text(value, field):
    require(isinstance(value, str) and 0 < len(value) <= 4096 and not any(ord(c) < 32 or ord(c) == 127 for c in value), "invalid " + field)


def url(value):
    require(isinstance(value, str) and len(value) <= 2048 and URL.fullmatch(value), "sources/repositories must be credential-free HTTPS URLs")


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 240 and re.fullmatch(r"[A-Za-z0-9_.+@ /()-]+", value), "unsafe metadata path")
    for part in value.split("/"):
        require(part not in {"", ".", ".."} and not part.endswith((" ", ".")), "unsafe metadata path")
        stem = part.lower().split(".", 1)[0]
        require(stem not in {"con", "prn", "aux", "nul"} and not re.fullmatch(r"(?:com|lpt)[0-9]", stem), "reserved metadata path")


def metadata(data, expected=None):
    require(isinstance(data, dict), "package metadata must be an object")
    package = name(data.get("name"))
    require(expected is None or package == expected, "package identity mismatch")
    text(data.get("description"), "description")
    url(data.get("repository"))
    if "license" in data:
        text(data["license"], "license")
    versions = data.get("versions")
    require(isinstance(versions, dict) and 0 < len(versions) <= 1000, "invalid version map")
    for ver, record in versions.items():
        version(ver)
        require(isinstance(record, dict), "version metadata must be an object")
        require("native" not in record and "capabilities" not in record, "native capabilities are unsupported")
        source = record.get("source")
        require(isinstance(source, dict) and source.get("type") == "git", "only Git sources are supported")
        url(source.get("url"))
        require(isinstance(source.get("revision"), str) and re.fullmatch(r"[0-9a-f]{40}", source["revision"]), "source needs an immutable 40-character Git commit")
        require(isinstance(record.get("integrity"), str) and re.fullmatch(r"[0-9a-f]{64}", record["integrity"]), "version needs its SHA-256 integrity checksum")
        relative(record.get("entry"))
        require(record["entry"].endswith((".lua", ".luau")), "entrypoint must be .lua/.luau")
        dependencies = record.setdefault("dependencies", {})
        require(isinstance(dependencies, dict) and len(dependencies) <= 256, "invalid dependencies")
        for dependency, spec in dependencies.items():
            name(dependency)
            version_range(spec)
        if package == "xtpkg":
            require(data["repository"].removesuffix(".git") in OFFICIAL and source["url"].removesuffix(".git") in OFFICIAL, "xtpkg is reserved for its official upstream repositories")
    return data


def pairs(values):
    data = {}
    for key, value in values:
        require(key not in data, "duplicate JSON key: " + key)
        data[key] = value
    return data


def bounded(value, depth=0, budget=None):
    budget = budget or [0]
    budget[0] += 1
    require(budget[0] <= 100000, "excessive JSON node count")
    require(depth <= 40, "excessive JSON nesting")
    if isinstance(value, dict):
        for item in value.values():
            bounded(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            bounded(item, depth + 1, budget)


def safe(root, value):
    relative(value)
    root = Path(root).absolute()
    for ancestor in [root, *root.parents]:
        require(not ancestor.is_symlink() and not getattr(ancestor, "is_junction", lambda: False)(), "registry root contains a link")
    current = root
    for part in value.split("/"):
        current /= part
        require(not current.is_symlink() and not getattr(current, "is_junction", lambda: False)(), "metadata symlinks/junctions are forbidden")
    require(current.resolve().is_relative_to(root.resolve()), "metadata path escapes registry")
    return current


def read(root, value):
    filename = safe(root, value)
    require(filename.is_file() and filename.stat().st_size <= MAX_BYTES, "missing/oversized metadata: " + value)
    def invalid_constant(value):
        raise ValueError("non-finite JSON number")
    data = json.loads(filename.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
    bounded(data)
    return data


def load(root):
    index = read(root, "index.json")
    require(isinstance(index, dict) and type(index.get("registry_version")) is int and index["registry_version"] == 1 and isinstance(index.get("packages"), dict), "invalid registry index")
    require(len(index["packages"]) <= 10000, "registry index exceeds package limit")
    packages = {}
    for package, record in index["packages"].items():
        name(package)
        require(isinstance(record, dict) and record.get("path") == "packages/" + package + ".json", "index path must match package identity")
        if "description" in record:
            text(record["description"], "index description")
        packages[package] = metadata(read(root, record["path"]), package)
    # Every published metadata record must be indexed, and symlinks are refused.
    package_root = Path(root) / "packages"
    if package_root.exists():
        safe(root, "packages")
        for directory, directories, files in os.walk(package_root, followlinks=False):
            for child in directories + files:
                relative_path = (Path(directory) / child).relative_to(root).as_posix()
                safe(root, relative_path)
            for child in files:
                require(child.endswith(".json"), "unexpected published package file")
                identity = (Path(directory) / child).relative_to(package_root).as_posix()[:-5]
                require(identity in packages, "published package is missing from index: " + identity)
    return index, packages


def same_record(left, right):
    # Python considers True == 1; immutable JSON records must distinguish them.
    return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)


def immutable(previous, candidate):
    for package, old in previous.items():
        require(package in candidate, "existing package was removed: " + package)
        for ver, record in old["versions"].items():
            require(same_record(candidate[package]["versions"].get(ver), record), "immutable version changed/removed: " + package + "@" + ver)


def pending(root):
    directory = Path(root) / "submissions"
    safe(root, "submissions")
    if not directory.exists():
        return []
    result = []
    for entry in sorted(directory.iterdir()):
        safe(root, entry.relative_to(root).as_posix())
        if entry.name == "README.md":
            continue
        require(entry.is_file() and entry.suffix == ".json", "submissions must be flat JSON files")
        result.append((entry.relative_to(root).as_posix(), metadata(read(root, entry.relative_to(root).as_posix()))))
        require(len(result) <= 100, "submission queue exceeds 100 files")
    return result


def plan(root, include_submissions):
    index, existing = load(root)
    packages = copy.deepcopy(existing)
    queued = pending(root) if include_submissions else []
    for _, submission in queued:
        package = submission["name"]
        if package in packages:
            current = packages[package]
            for ver, record in submission["versions"].items():
                require(ver not in current["versions"] or same_record(current["versions"][ver], record), "submission rewrites immutable version: " + package + "@" + ver)
                current["versions"][ver] = record
            for key, value in submission.items():
                if key != "versions":
                    current[key] = value
        else:
            packages[package] = submission
        index["packages"].setdefault(package, {})
        index["packages"][package].update(path="packages/" + package + ".json", description=submission["description"])
    for package, data in packages.items():
        metadata(data, package)
    immutable(existing, packages)
    require(len(packages) <= 10000, "registry exceeds package limit")
    pretty(index)
    for data in packages.values():
        pretty(data)
    return index, packages, queued


def pretty(data):
    encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    require(len(encoded) <= MAX_BYTES, "formatted metadata exceeds size limit")
    return encoded


def apply(root, index, packages, queued):
    outputs = {"index.json": pretty(index)}
    outputs.update({"packages/" + package + ".json": pretty(data) for package, data in packages.items()})
    outputs.update({filename: None for filename, _ in queued})
    before, changed = {}, []
    try:
        for relative_path, contents in outputs.items():
            target = safe(root, relative_path)
            original = target.read_bytes() if target.exists() else None
            if original == contents:
                continue
            before[relative_path] = original
            if contents is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                safe(root, relative_path)
                temp = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                        temp = Path(temporary.name)
                        temporary.write(contents)
                        temporary.flush()
                    os.chmod(temp, target.stat().st_mode & 0o777 if target.exists() else 0o644)
                    os.replace(temp, target)
                finally:
                    if temp is not None:
                        temp.unlink(missing_ok=True)
            changed.append(relative_path)
    except BaseException:
        for relative_path in reversed(changed):
            target = safe(root, relative_path)
            if before[relative_path] is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(before[relative_path])
        raise
    return len(changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "apply", "format"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--submissions", action="store_true")
    args = parser.parse_args()
    root = args.root.absolute()
    index, packages, queued = plan(root, args.submissions or args.command == "apply")
    if args.previous:
        _, previous = load(args.previous.absolute())
        immutable(previous, packages)
    if args.command == "validate":
        print(f"Validated {len(packages)} packages and {len(queued)} queued submissions")
    else:
        changed = apply(root, index, packages, queued)
        print(f"Updated {changed} metadata/submission files")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RecursionError) as error:
        print("error: " + str(error), file=sys.stderr)
        sys.exit(1)
