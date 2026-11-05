"""Scrub credentials from a CI artifact directory before it is uploaded.

The enclave repository is public, so run artifacts can be downloaded by any
logged-in GitHub user. Every file under the directory is processed in place:

- text files are redacted: pull-secret auth values, discovery ISO download
  tokens, JWTs and other bearer tokens, credentialed URLs, kubeconfig client
  credentials and private keys;
- archives (.tar, .tar.gz, .tgz, .zip) are extracted next to themselves,
  removed, and their contents scrubbed like any other file;
- anything that cannot be scanned (binary files, unreadable archives) and
  kubeconfig files are removed.

A scrub-report.txt with per-file match counts (never values) is written at the
top of the directory.
"""

import re
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import click

REDACTED = "REDACTED"
REPORT_NAME = "scrub-report.txt"

# Ordered: specific patterns first so a generic one does not consume their context.
# Each entry is (name, compiled pattern, replacement).
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        f"-----{REDACTED} PRIVATE KEY-----",
    ),
    (
        # Pull secret / docker config auth values, also inside escaped JSON.
        "auth-field",
        re.compile(
            r'(\\*"(?:auth|password|identitytoken|registrytoken)\\*"\s*:\s*\\*")'
            r'[^"\\]+'
        ),
        rf"\g<1>{REDACTED}",
    ),
    (
        "dockerconfigjson",
        re.compile(r'(\.dockerconfigjson\\*"?\s*[:=]\s*\\*"?)[A-Za-z0-9+/=]{16,}'),
        rf"\g<1>{REDACTED}",
    ),
    (
        "kubeconfig-credential",
        re.compile(
            r"^(\s*(?:client-certificate-data|client-key-data|token|password)\s*:\s*)"
            r"\S+",
            re.MULTILINE,
        ),
        rf"\g<1>{REDACTED}",
    ),
    (
        # Assisted-service discovery ISO URLs: the ISO embeds the pull secret.
        "iso-download-token",
        re.compile(r"(/byapikey/)[^/\s\"']+"),
        rf"\g<1>{REDACTED}",
    ),
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        REDACTED,
    ),
    (
        "openshift-token",
        re.compile(r"sha256~[A-Za-z0-9_-]{20,}"),
        f"sha256~{REDACTED}",
    ),
    (
        "bearer-token",
        re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE),
        rf"\g<1>{REDACTED}",
    ),
    (
        "token-query-param",
        re.compile(
            r"([?&](?:api_key|access_token|token|sig|signature)=)[^&\s\"']+",
            re.IGNORECASE,
        ),
        rf"\g<1>{REDACTED}",
    ),
    (
        "url-credentials",
        re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@\"']+:[^/\s@\"']+@"),
        rf"\g<1>{REDACTED}@",
    ),
]

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")


class ScrubError(Exception):
    """Raised when the artifact directory cannot be scrubbed."""


@dataclass
class ScrubReport:
    """What the scrubber changed, without any matched values."""

    redactions: dict[str, Counter[str]] = field(default_factory=dict)
    removed: dict[str, str] = field(default_factory=dict)
    extracted: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = ["Artifact scrub report (counts only, no values)", ""]
        total = sum(sum(c.values()) for c in self.redactions.values())
        lines.append(f"Redactions: {total} in {len(self.redactions)} file(s)")
        for path in sorted(self.redactions):
            counts = ", ".join(
                f"{name}={n}" for name, n in sorted(self.redactions[path].items())
            )
            lines.append(f"  {path}: {counts}")
        lines.append(f"Archives extracted: {len(self.extracted)}")
        lines.extend(f"  {path}" for path in sorted(self.extracted))
        lines.append(f"Files removed: {len(self.removed)}")
        lines.extend(
            f"  {path}: {reason}" for path, reason in sorted(self.removed.items())
        )
        return "\n".join(lines) + "\n"


def scrub_text(text: str) -> tuple[str, Counter[str]]:
    """Redact every known credential pattern; return the text and match counts.

    Only matches whose replacement changes the text are counted, so already
    redacted values (e.g. a re-run over a scrubbed bundle) are not reported.
    """
    counts: Counter[str] = Counter()
    for name, pattern, replacement in PATTERNS:

        def _replace(
            m: re.Match[str], name: str = name, repl: str = replacement
        ) -> str:
            new = m.expand(repl)
            if new != m.group(0):
                counts[name] += 1
            return new

        text = pattern.sub(_replace, text)
    return text, counts


def _is_kubeconfig(path: Path) -> bool:
    return "kubeconfig" in path.name.lower()


def _archive_suffix(path: Path) -> str:
    name = path.name.lower()
    return next((s for s in ARCHIVE_SUFFIXES if name.endswith(s)), "")


def _safe_target(base: Path, member: str) -> bool:
    """True if extracting member under base stays inside base."""
    target = (base / member).resolve()
    return target == base.resolve() or base.resolve() in target.parents


def _extract(archive: Path, suffix: str) -> Path:
    dest = archive.with_name(archive.name[: -len(suffix)] + ".extracted")
    dest.mkdir(parents=True, exist_ok=True)
    if suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            for name in names:
                if not _safe_target(dest, name):
                    raise ScrubError(f"unsafe path in archive: {name}")
            for name in names:
                zf.extract(name, dest)
    else:
        with tarfile.open(archive) as tf:
            members = [m for m in tf.getmembers() if m.isfile() or m.isdir()]
            for m in members:
                if not _safe_target(dest, m.name):
                    raise ScrubError(f"unsafe path in archive: {m.name}")
            tf.extractall(dest, members=members, filter="data")
    return dest


def _scrub_file(path: Path, root: Path, report: ScrubReport) -> None:
    rel = str(path.relative_to(root))
    if _is_kubeconfig(path):
        path.unlink()
        report.removed[rel] = "kubeconfig"
        return
    suffix = _archive_suffix(path)
    if suffix:
        try:
            dest = _extract(path, suffix)
        except (tarfile.TarError, zipfile.BadZipFile, OSError, ScrubError) as exc:
            path.unlink()
            report.removed[rel] = f"archive could not be extracted safely ({exc})"
            return
        path.unlink()
        report.extracted.append(rel)
        _scrub_tree(dest, root, report)
        return
    data = path.read_bytes()
    if b"\x00" in data:
        path.unlink()
        report.removed[rel] = "binary file (cannot be scanned)"
        return
    text = data.decode("utf-8", errors="replace")
    scrubbed, counts = scrub_text(text)
    if counts:
        path.write_text(scrubbed, encoding="utf-8")
        report.redactions[rel] = counts


def _scrub_tree(directory: Path, root: Path, report: ScrubReport) -> None:
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            path.unlink()
            report.removed[str(path.relative_to(root))] = "symlink"
        elif path.is_file():
            _scrub_file(path, root, report)


def scrub_directory(root: Path) -> ScrubReport:
    """Scrub every file under root in place and write the report there."""
    if not root.is_dir():
        raise ScrubError(f"not a directory: {root}")
    report = ScrubReport()
    _scrub_tree(root, root, report)
    (root / REPORT_NAME).write_text(report.render(), encoding="utf-8")
    return report


@click.command()
@click.argument(
    "directories",
    nargs=-1,
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def cli(directories: tuple[Path, ...]) -> None:
    """Scrub credentials from artifact DIRECTORIES in place before upload."""
    for directory in directories:
        if not directory.exists():
            click.echo(f"{directory}: does not exist, skipping")
            continue
        try:
            report = scrub_directory(directory)
        except ScrubError as exc:
            raise click.ClickException(str(exc)) from exc
        total = sum(sum(c.values()) for c in report.redactions.values())
        click.echo(
            f"{directory}: {total} redaction(s) in {len(report.redactions)} file(s), "
            f"{len(report.extracted)} archive(s) extracted, "
            f"{len(report.removed)} file(s) removed"
        )
