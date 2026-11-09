# GitHub preparation and release

Suggested repository name: `electrode-monitor`.

## Upload

The source is prepared locally; preparation does not publish a remote repository.

Use the clean archive from `python tools/package_source.py`, or commit the source files with Git. Do not upload the entire working folder: it may contain private measurements, local dependencies, and a virtual environment. The ignore rules and packaging allowlist exclude these files.

The original application uses the MIT license. Keep its license file and required dependency notices in distributions.

Create an empty repository in your chosen account, choose visibility, and connect the local repository to its URL. Commit and push the prepared files to `main`. No credentials or account names are stored in the project.

## Checks and artifacts

The `Test and package` workflow runs on pushes, pull requests, and manual invocation, with read-only repository permissions. It never touches hardware. The `electrode-monitor-distributions` artifact contains a source distribution and wheel.

Locally, install `build` and run `python -m build`. Run `python tools/package_source.py` for a GitHub-ready zip. This tool uses an explicit allowlist and includes the workflow, source, tests, setup/launch scripts, and documentation. It excludes private data, downloaded libraries, caches, Git history, local settings, and screenshots.

The wheel installs three modules and metadata, declares the serial dependency, and requires Python with Tkinter. It is not a standalone executable.

## Release

1. Run offline tests and a disconnected GUI smoke test.
2. Build distributions and inspect their contents.
3. Run the explicit hardware test only with an intentionally available, idle instrument.
4. Review version, license, and release notes.
5. Create a version tag and release, then attach verified artifacts.

There is no automatic publication step. Communication has been verified with firmware 716.0022; document testing before claiming support for other firmware.
