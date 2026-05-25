"""manage.py read_logs view|tail|trace|heartbeats."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404: only ever invokes `less` with a fixed argv.
import sys
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.observability.log_reader import LogFilter, iter_events


class Command(BaseCommand):
    help = "Read structured log records."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)

        view = sub.add_parser("view", help="Print filtered records (one-shot).")
        for p in (view,):
            p.add_argument("--category")
            p.add_argument(
                "--level",
                choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            )
            p.add_argument("--logger")
            p.add_argument("--trace-id", dest="trace_id")
            p.add_argument("--run-id", dest="run_id")
            p.add_argument("--incident", type=int, dest="incident_id")
            p.add_argument("--since")
            p.add_argument("--until")
            p.add_argument("--grep")
            p.add_argument(
                "--last",
                type=int,
                default=None,
                help="Cap output to the most recent N records (default: unlimited).",
            )
            p.add_argument("--stream", choices=["events", "heartbeats"], default="events")
            p.add_argument("--instance", help="Read from LOGS_DIR/cluster/<instance>/ instead.")
            p.add_argument("--json", action="store_true")
            p.add_argument("--plain", action="store_true")
            p.add_argument("--no-pager", action="store_true")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "view":
            return self._view(options)
        raise NotImplementedError(action)

    def _logs_dir(self, instance: str | None) -> Path:
        base = Path(settings.LOGS_DIR).resolve()
        if not instance:
            return base
        cluster_root = (base / "cluster").resolve()
        target = (cluster_root / instance).resolve()
        if not target.is_relative_to(cluster_root):
            raise CommandError(
                f"--instance must be a simple name inside {cluster_root}; got {instance!r}"
            )
        return target

    def _view(self, options):
        flt = LogFilter(
            category=options.get("category"),
            level=options.get("level"),
            logger=options.get("logger"),
            trace_id=options.get("trace_id"),
            run_id=options.get("run_id"),
            incident_id=options.get("incident_id"),
            since=options.get("since"),
            until=options.get("until"),
            grep=options.get("grep"),
            last=options.get("last"),
        )
        stream = options["stream"]
        basename = "events.jsonl" if stream == "events" else "heartbeats.jsonl"
        logs_dir = self._logs_dir(options.get("instance"))

        records = iter_events(logs_dir, flt, basename=basename)

        def lines() -> Iterable[str]:
            for rec in records:
                if options.get("json"):
                    yield json.dumps(rec, ensure_ascii=False)
                else:
                    yield self._fmt_pretty(rec, plain=options.get("plain", False))

        self._emit(lines(), use_pager=not options.get("no_pager"))

    def _emit(self, lines: Iterable[str], use_pager: bool) -> None:
        # Stream through `less -FRX` when running interactively so output
        # flows as records are parsed and the pager exits cleanly if the
        # result fits one screen. Skip the pager on non-TTY (CI, redirected
        # stdout) or when `less` is not installed — fall back to direct
        # stdout writes in those cases. argv[0] is always the fully resolved
        # absolute path returned by shutil.which, never a bare name.
        pager_path = shutil.which("less") if use_pager and sys.stdout.isatty() else None
        if pager_path is None:
            for line in lines:
                self.stdout.write(line)
            return

        # nosec B603: argv is a fixed two-element list; pager_path is the
        # resolved absolute path from shutil.which (no PATH lookup at exec).
        pager = subprocess.Popen(  # nosec B603
            [pager_path, "-FRX"],
            stdin=subprocess.PIPE,
            text=True,
        )
        assert pager.stdin is not None  # for type-checkers; PIPE guarantees stdin
        try:
            for line in lines:
                pager.stdin.write(line + "\n")
        except BrokenPipeError:
            pass  # user quit the pager mid-stream
        finally:
            try:
                pager.stdin.close()
            except BrokenPipeError:
                pass
            pager.wait()

    def _fmt_pretty(self, rec: dict, plain: bool) -> str:
        if plain:
            return rec.get("msg", "")
        time_part = rec.get("ts", "")[11:19]
        level = rec.get("level", "")
        logger = rec.get("logger", "")
        msg = rec.get("msg", "")
        trace = rec.get("trace_id", "")[:8] if rec.get("trace_id") else ""
        line = f"{time_part}  {level:<5}  {logger}  {msg}"
        if trace:
            line += f"  trace={trace}"
        return line
