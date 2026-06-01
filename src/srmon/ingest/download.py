"""Download system-resource-monitor JSONL logs from a server over SSH and keep a
per-host combined file under data/ up to date.

The local file is the persistent store. Each run fetches only the *delta* since
the latest sample already stored (the remote command only `cat`s daily files on
or after the local cutoff date) and APPENDS the new, deduplicated lines in place —
the existing body is never re-downloaded or rewritten. The per-host filename keeps
its `<host>_<start>_to_<end>.jsonl` form; only the `_to_<end>` is bumped (a cheap
rename) when newly appended data extends the range.
"""

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from srmon.core.naming import build_combined_log_filename, parse_combined_log_name
from srmon.core.samples import parse_sample_date_from_line
from srmon.core.selection import DEFAULT_DOWNLOADED_LOG_DIR


DEFAULT_REMOTE_LOG_DIR = "/var/log/system-resource-monitor"
DEFAULT_OUTPUT_DIR = DEFAULT_DOWNLOADED_LOG_DIR


@dataclass(frozen=True)
class MergeResult:
    path: Path
    hostname: str
    start_date: date
    end_date: date
    existing_rows: int  # boundary-day rows compared for dedup (0 on a first/full write)
    downloaded_rows: int
    appended_rows: int
    duplicate_rows: int


class RemoteCommandError(RuntimeError):
    pass


def sanitize_hostname(hostname: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", hostname.strip()).strip("._-")
    if not cleaned:
        raise ValueError("Remote hostname is empty after sanitizing")
    return cleaned


def find_existing_host_logs(output_dir: Path, hostname: str) -> List[Path]:
    existing: List[Path] = []
    for path in output_dir.glob(f"{hostname}_*_to_*.jsonl"):
        parsed = parse_combined_log_name(path)
        if parsed is not None and parsed.hostname == hostname:
            existing.append(path)
    return sorted(existing)


def normalize_log_line(raw_line: str) -> Optional[str]:
    line = raw_line.rstrip("\r\n")
    if not line.strip():
        return None
    return f"{line}\n"


def log_line_digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def parse_sample_date(line: str) -> Optional[date]:
    return parse_sample_date_from_line(line, None)


def extend_date_range(
    start_date: Optional[date],
    end_date: Optional[date],
    sample_date: Optional[date],
) -> Tuple[Optional[date], Optional[date]]:
    if sample_date is None:
        return start_date, end_date
    if start_date is None or sample_date < start_date:
        start_date = sample_date
    if end_date is None or sample_date > end_date:
        end_date = sample_date
    return start_date, end_date


# --- tail helpers (read only the end of a possibly huge file) ---------------


def _iter_lines_backward(path: Path, block_size: int = 65536) -> Iterable[str]:
    """Yield decoded lines from EOF toward BOF (may include empty strings)."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        remaining = handle.tell()
        tail = b""
        while remaining > 0:
            read_size = min(block_size, remaining)
            remaining -= read_size
            handle.seek(remaining)
            data = handle.read(read_size) + tail
            parts = data.split(b"\n")
            tail = parts[0]
            for part in reversed(parts[1:]):
                yield part.decode("utf-8", "replace")
        yield tail.decode("utf-8", "replace")


def read_local_cutoff_date(path: Path) -> Optional[date]:
    """UTC date of the latest stored sample (last complete line), or None."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    for raw in _iter_lines_backward(path):
        line = raw.strip()
        if not line:
            continue
        return parse_sample_date(line)
    return None


def collect_cutoff_day_hashes(path: Path, cutoff_date: Optional[date]) -> Set[str]:
    """sha256 of local lines whose date == cutoff_date, scanning backward from EOF
    until the date rolls earlier. Bounded to ~one day, not the whole file."""
    hashes: Set[str] = set()
    if cutoff_date is None or not path.exists():
        return hashes
    for raw in _iter_lines_backward(path):
        line = raw.strip()
        if not line:
            continue
        sample_date = parse_sample_date(line)
        if sample_date is None:
            continue
        if sample_date == cutoff_date:
            normalized = normalize_log_line(line)
            if normalized is not None:
                hashes.add(log_line_digest(normalized))
        elif sample_date < cutoff_date:
            break
    return hashes


def repair_partial_tail(path: Path) -> None:
    """Drop a half-written final line left by an interrupted append (no trailing
    newline). O(1)-ish: truncate back to the last newline; no full rewrite."""
    if not path.exists():
        return
    size = path.stat().st_size
    if size == 0:
        return
    with path.open("rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return
        remaining = size
        last_newline = -1
        while remaining > 0:
            read_size = min(65536, remaining)
            remaining -= read_size
            handle.seek(remaining)
            data = handle.read(read_size)
            idx = data.rfind(b"\n")
            if idx != -1:
                last_newline = remaining + idx
                break
        handle.truncate(last_newline + 1 if last_newline != -1 else 0)


# --- merge / append ---------------------------------------------------------


def _full_write(
    safe_hostname: str, remote_lines: Iterable[str], output_dir: Path, existing_paths: List[Path]
) -> MergeResult:
    """First download (no existing file) or consolidation (>1 existing): write a
    fresh file from existing + remote, deduped, named by the min/max date seen."""
    temp_path = output_dir / f".{safe_hostname}.download.tmp"
    seen_hashes: Set[str] = set()
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    existing_rows = downloaded_rows = appended_rows = duplicate_rows = 0

    try:
        with temp_path.open("w", encoding="utf-8") as output:
            for path in existing_paths:
                parsed = parse_combined_log_name(path)
                if parsed is not None:
                    start_date, end_date = extend_date_range(start_date, end_date, parsed.start_date)
                    start_date, end_date = extend_date_range(start_date, end_date, parsed.end_date)
                with path.open(encoding="utf-8") as existing_file:
                    for raw_line in existing_file:
                        line = normalize_log_line(raw_line)
                        if line is None:
                            continue
                        digest = log_line_digest(line)
                        if digest in seen_hashes:
                            continue
                        seen_hashes.add(digest)
                        start_date, end_date = extend_date_range(start_date, end_date, parse_sample_date(line))
                        output.write(line)
                        existing_rows += 1

            for raw_line in remote_lines:
                line = normalize_log_line(raw_line)
                if line is None:
                    continue
                downloaded_rows += 1
                start_date, end_date = extend_date_range(start_date, end_date, parse_sample_date(line))
                digest = log_line_digest(line)
                if digest in seen_hashes:
                    duplicate_rows += 1
                    continue
                seen_hashes.add(digest)
                output.write(line)
                appended_rows += 1
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    if not seen_hashes:
        temp_path.unlink(missing_ok=True)
        raise ValueError("No log rows found locally or on the remote server")
    if start_date is None or end_date is None:
        temp_path.unlink(missing_ok=True)
        raise ValueError("No valid timestamps found, so the local log filename cannot be built")

    final_path = output_dir / build_combined_log_filename(safe_hostname, start_date, end_date)
    temp_path.replace(final_path)
    for path in existing_paths:
        if path != final_path and path.exists():
            path.unlink()

    return MergeResult(
        path=final_path,
        hostname=safe_hostname,
        start_date=start_date,
        end_date=end_date,
        existing_rows=existing_rows,
        downloaded_rows=downloaded_rows,
        appended_rows=appended_rows,
        duplicate_rows=duplicate_rows,
    )


def _incremental_append(
    safe_hostname: str, remote_lines: Iterable[str], output_dir: Path, existing_path: Path
) -> MergeResult:
    """Append only new lines to the existing per-host file in place; bump the
    filename's end-date if the range grew. Never reads/rewrites the existing body."""
    parsed = parse_combined_log_name(existing_path)
    repair_partial_tail(existing_path)
    cutoff = read_local_cutoff_date(existing_path)
    boundary = collect_cutoff_day_hashes(existing_path, cutoff)

    downloaded_rows = appended_rows = duplicate_rows = 0
    min_new: Optional[date] = None
    max_new: Optional[date] = None
    saw_cutoff_day = False

    with existing_path.open("a", encoding="utf-8") as output:
        for raw_line in remote_lines:
            line = normalize_log_line(raw_line)
            if line is None:
                continue
            downloaded_rows += 1
            sample_date = parse_sample_date(line)
            if cutoff is not None and sample_date is not None:
                if sample_date < cutoff:
                    continue  # already stored (older than our tail)
                if sample_date == cutoff:
                    saw_cutoff_day = True
                    if log_line_digest(line) in boundary:
                        duplicate_rows += 1
                        continue
            output.write(line)
            appended_rows += 1
            min_new, max_new = extend_date_range(min_new, max_new, sample_date)
        output.flush()
        os.fsync(output.fileno())

    start_date = parsed.start_date if parsed is not None else (min_new or cutoff)
    end_date = parsed.end_date if parsed is not None else (max_new or cutoff)
    if max_new is not None and (end_date is None or max_new > end_date):
        end_date = max_new

    final_path = existing_path
    if parsed is not None and end_date is not None and end_date != parsed.end_date:
        final_path = output_dir / build_combined_log_filename(safe_hostname, parsed.start_date, end_date)
        existing_path.replace(final_path)

    if (
        cutoff is not None
        and appended_rows
        and not saw_cutoff_day
        and min_new is not None
        and (min_new - cutoff).days > 1
    ):
        sys.stderr.write(
            f"Warning: gap for {safe_hostname}: local data ends {cutoff.isoformat()}, "
            f"server's earliest new data is {min_new.isoformat()}; intervening days "
            "were pruned on the server.\n"
        )

    return MergeResult(
        path=final_path,
        hostname=safe_hostname,
        start_date=start_date if start_date is not None else end_date,
        end_date=end_date,
        existing_rows=len(boundary),
        downloaded_rows=downloaded_rows,
        appended_rows=appended_rows,
        duplicate_rows=duplicate_rows,
    )


def merge_lines_into_host_log(hostname: str, remote_lines: Iterable[str], output_dir: Path) -> MergeResult:
    safe_hostname = sanitize_hostname(hostname)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_paths = find_existing_host_logs(output_dir, safe_hostname)
    if len(existing_paths) == 1:
        return _incremental_append(safe_hostname, remote_lines, output_dir, existing_paths[0])
    return _full_write(safe_hostname, remote_lines, output_dir, existing_paths)


# --- SSH ---------------------------------------------------------------------


def build_ssh_base_command_from_options(
    server: str,
    *,
    port: Optional[int] = None,
    identity_file: Optional[str] = None,
    ssh_options: Sequence[str] = (),
) -> List[str]:
    command = ["ssh"]
    if port is not None:
        command.extend(["-p", str(port)])
    if identity_file:
        command.extend(["-i", str(Path(identity_file).expanduser())])
    for option in ssh_options:
        command.extend(["-o", option])
    command.append(server)
    return command


def run_remote_text(ssh_base_command: Sequence[str], remote_command: str) -> str:
    result = subprocess.run(
        [*ssh_base_command, remote_command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RemoteCommandError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
    return result.stdout


def read_remote_hostname(ssh_base_command: Sequence[str]) -> str:
    output = run_remote_text(ssh_base_command, "hostname 2>/dev/null || uname -n")
    for line in output.splitlines():
        hostname = line.strip()
        if hostname:
            return hostname
    raise RemoteCommandError("Remote hostname command returned no output")


def build_remote_cat_command(remote_log_dir: str, since_date: Optional[date] = None) -> str:
    """Remote sh that cats daily files whose date >= since_date (all if None).

    POSIX-safe: dates are zero-padded YYYY-MM-DD, so `since` sorts <= `d` exactly
    when since is chronologically on/before d. Uses printf|sort|head (no bashisms).
    """
    quoted_dir = shlex.quote(remote_log_dir)
    quoted_since = shlex.quote(since_date.isoformat() if since_date else "")
    return (
        f"log_dir={quoted_dir}; since={quoted_since}; "
        'if [ ! -d "$log_dir" ]; then '
        'echo "Remote log directory does not exist: $log_dir" >&2; exit 2; fi; '
        'find "$log_dir" -maxdepth 1 -type f -name \'metrics-*.jsonl\' -print | sort | '
        'while IFS= read -r file; do '
        'base=${file##*/}; d=${base#metrics-}; d=${d%.jsonl}; '
        'if [ -z "$since" ] || '
        '[ "$(printf \'%s\\n%s\\n\' "$since" "$d" | sort | head -n 1)" = "$since" ]; then '
        'cat "$file"; fi; '
        "done"
    )


def stream_remote_log_lines(
    ssh_base_command: Sequence[str], remote_log_dir: str, since_date: Optional[date] = None
) -> Iterable[str]:
    process = subprocess.Popen(
        [*ssh_base_command, build_remote_cat_command(remote_log_dir, since_date)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        for line in process.stdout:
            yield line
        stderr = process.stderr.read()
        returncode = process.wait()
        if returncode != 0:
            raise RemoteCommandError(stderr.strip() or "ssh log download failed")
    finally:
        if process.poll() is None:
            process.terminate()


def download_host_logs(
    server: str,
    *,
    output_dir: Path,
    remote_log_dir: str = DEFAULT_REMOTE_LOG_DIR,
    hostname: Optional[str] = None,
    port: Optional[int] = None,
    identity_file: Optional[str] = None,
    ssh_options: Sequence[str] = (),
) -> MergeResult:
    """Download the delta + append to the host's combined file. Reusable by the report."""
    ssh_base_command = build_ssh_base_command_from_options(
        server, port=port, identity_file=identity_file, ssh_options=ssh_options
    )
    resolved_hostname = hostname or read_remote_hostname(ssh_base_command)
    output_dir = Path(output_dir).expanduser()

    # If there's exactly one existing per-host file, fetch only data on/after its
    # latest stored date (the cutoff). Otherwise fetch everything.
    existing = find_existing_host_logs(output_dir, sanitize_hostname(resolved_hostname))
    since = read_local_cutoff_date(existing[0]) if len(existing) == 1 else None

    return merge_lines_into_host_log(
        hostname=resolved_hostname,
        remote_lines=stream_remote_log_lines(ssh_base_command, remote_log_dir, since),
        output_dir=output_dir,
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("server", help="SSH target, for example robotruck@100.64.0.6")
    parser.add_argument(
        "--remote-log-dir",
        default=DEFAULT_REMOTE_LOG_DIR,
        help=f"Remote directory containing metrics-YYYY-MM-DD.jsonl files. Default: {DEFAULT_REMOTE_LOG_DIR}",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Local directory for downloaded logs. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--hostname", default=None, help="Override the remote hostname used in the local filename.")
    parser.add_argument("--port", type=int, default=None, help="SSH port.")
    parser.add_argument("--identity-file", default=None, help="SSH private key path.")
    parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        help="Extra ssh -o option. Repeat for multiple options, for example --ssh-option ConnectTimeout=10.",
    )


def run(args: argparse.Namespace) -> int:
    try:
        result = download_host_logs(
            args.server,
            output_dir=Path(args.output_dir).expanduser(),
            remote_log_dir=args.remote_log_dir,
            hostname=args.hostname,
            port=args.port,
            identity_file=args.identity_file,
            ssh_options=args.ssh_option,
        )
    except (RemoteCommandError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Server: {args.server}")
    print(f"Hostname: {result.hostname}")
    print(f"Local file: {result.path}")
    print(f"Date range: {result.start_date.isoformat()} -> {result.end_date.isoformat()}")
    print(f"Downloaded rows: {result.downloaded_rows}")
    print(f"Appended rows: {result.appended_rows}")
    print(f"Duplicate rows skipped: {result.duplicate_rows}")
    if result.existing_rows:
        print(f"Boundary rows compared: {result.existing_rows}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download new system-resource-monitor logs from a server and append them locally."
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
