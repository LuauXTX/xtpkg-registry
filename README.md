# XTPKG registry

The public, Git-backed registry for LuauXTX packages. Installations need no GitHub
authentication. XTPKG defaults to:

```text
https://raw.githubusercontent.com/LuauXTX/xtpkg-registry/main
```

Override it with `XTPKG_REGISTRY` or `xtpkg config set registry <base URL>`.
`index.json` provides discovery; immutable package versions live in
`packages/<name>.json` or `packages/<scope>/<name>.json`. JSON uses UTF-8, sorted
keys, two-space indentation, and a final newline.

## Submit a package or version

1. Commit a valid package manifest, entrypoint, and licence file in the source
   repository. Use pure Luau packages and registry version ranges for dependencies.
2. Run `xtpkg publish --output my-package-submission.json` and push the exact
   commit referenced by the output. Publishing prepares JSON; it does not upload.
3. Open a PR adding that JSON under `submissions/`, for example
   `submissions/example-json-1.0.0.json`. The inbox is flat and accepts at most
   100 pending files. One submission may contain multiple versions.
4. Maintainers review package ownership, source code, licence, dependencies,
   commit availability, and actual checksum, then merge the submission PR.
5. `Prepare approved submissions` validates the queue and opens a generated PR
   updating package records and the index while removing processed inbox files.
6. Maintainers review and merge that generated PR to publish. Nothing auto-merges,
   overwrites immutable versions, or executes package source/install hooks.

Existing versions may be submitted again only when their metadata is identical.
Conflicting versions, invalid names/ranges, mutable refs, credentials in URLs,
unsafe paths, symlinks/junctions, oversized JSON, duplicate keys, unindexed
published records, and native capabilities are rejected. Every version needs
an exact 40-character lowercase Git commit, an entrypoint, and a 64-character
SHA-256 checksum. The `xtpkg` package is reserved for the official upstreams.

Static validation verifies metadata shape and immutability. It does not download
or run packages, prove authorship, or establish that a declared checksum matches
the remote tree. Maintainers can use XTPKG with a local candidate registry in a
temporary project to verify downloaded bytes without executing package code.

## Automation setup

The workflows use `GITHUB_TOKEN`; no personal access token or external write API
is required. In repository Actions settings, allow Actions to create pull
requests. Protect `main` with required maintainer review, up-to-date branches,
and metadata validation. Review changes to `.github/` and `tools/` carefully.
Merge this tooling infrastructure before using the submission checks, so the
base branch contains the trusted validator.

`Validate registry` executes the base branch's validator against candidate data
at a pinned head SHA. Candidate package files are parsed, never executed. Fork
checks need only read access. The publisher creates a fresh branch without force
pushes and will not overwrite an existing pending automation PR; merge/close it
and rerun the workflow to process any remaining queue.

GitHub may require approval for PR-triggered workflows created with
`GITHUB_TOKEN`. The publisher therefore explicitly dispatches `Validate registry`
on `main`, which validates the candidate and reports the `Registry metadata`
status on its exact head SHA. Maintainers may still need to approve other PR
workflow runs according to repository policy. The workflows can also be run
manually from the Actions tab. They are inactive until these commits are pushed.

## Local validation

Python 3.10+ and the standard library are sufficient:

```text
python tools/registry.py validate --submissions
python tools/registry.py validate --root candidate --previous previous --submissions
python tools/registry.py apply
python tools/registry.py format
python -m unittest discover -s tests -v
```

`validate` is read-only. `apply` validates the entire batch before writing,
preserves existing versions and metadata, updates the index, and removes accepted
inbox files. Ordinary write failures roll back changed files; in CI all work is
performed in a disposable checkout before a PR branch is pushed. `format` only
formats existing published metadata and does not consume pending submissions.
Existing release content checksums do not change when metadata JSON is formatted.
