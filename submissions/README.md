# Package submissions

Run `xtpkg publish --output my-package-submission.json` in a clean, committed
package repository. Push the referenced commit, then open a pull request adding
the generated JSON here as one flat `*.json` file. Do not edit existing published
versions. Libraries do not need executable entrypoints.

CI validates package names, paths, versions, ranges, HTTPS sources, immutable
commits, checksum syntax, and existing version immutability. It does not execute
package code or establish source ownership; maintainers still review the source,
licence, commit availability, and actual package checksum before approving.

After a submission PR merges, automation prepares a second PR containing the
merged package metadata and updated index, and removes processed inbox files.
Merging that generated PR publishes the new version. Nothing auto-merges.
