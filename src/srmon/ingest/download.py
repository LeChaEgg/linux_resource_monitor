"""Download all system-resource-monitor JSONL logs from a server over SSH and
merge them into a per-host combined file under local-debug-logs/.

The combined-log filename grammar and sample-date parsing are shared with the
rest of the package via srmon.core (no local duplicates).
"""

import argparse
import hashlib
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
    existing_rows: int
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


def merge_lines_into_host_log(hostname: str, remote_lines: Iterable[str], output_dir: Path) -> MergeResult:
    safe_hostname = sanitize_hostname(hostname)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_paths = find_existing_host_logs(output_dir, safe_hostname)
    temp_path = output_dir / f".{safe_hostname}.download.tmp"

    seen_hashes: Set[str] = set()
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    existing_rows = 0
    downloaded_rows = 0
    appended_rows = 0
    duplicate_rows = 0

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


def build_remote_cat_command(remote_log_dir: str) -> str:
    quoted_log_dir = shlex.quote(remote_log_dir)
    return (
        f"log_dir={quoted_log_dir}; "
        'if [ ! -d "$log_dir" ]; then '
        'echo "Remote log directory does not exist: $log_dir" >&2; exit 2; '
        "fi; "
        'find "$log_dir" -maxdepth 1 -type f -name "metrics-*.jsonl" -print | sort | '
        'while IFS= read -r file; do cat "$file"; done'
    )


def stream_remote_log_lines(ssh_base_command: Sequence[str], remote_log_dir: str) -> Iterable[str]:
    process = subprocess.Popen(
        [*ssh_base_command, build_remote_cat_command(remote_log_dir)],
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
    """Download + merge a host's logs. Reusable by the monthly report."""
    ssh_base_command = build_ssh_base_command_from_options(
        server, port=port, identity_file=identity_file, ssh_options=ssh_options
    )
    resolved_hostname = hostname or read_remote_hostname(ssh_base_command)
    return merge_lines_into_host_log(
        hostname=resolved_hostname,
        remote_lines=stream_remote_log_lines(ssh_base_command, remote_log_dir),
        output_dir=Path(output_dir).expanduser(),
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
        print(f"Existing rows kept: {result.existing_rows}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download all system-resource-monitor JSONL logs from a server into local-debug-logs."
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
