"""
Network connectivity checker (ping).
"""

import subprocess
import sys

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


class NetworkChecker(BaseChecker):
    """
    Check network connectivity by pinging hosts.

    Pings a list of hosts and reports connectivity status.
    Status is based on the percentage of reachable hosts.
    Default thresholds: warning if <70% reachable, critical if <50% reachable.
    """

    name = "network"
    warning_threshold = 70.0  # Minimum % of hosts that must be reachable for OK
    critical_threshold = (
        50.0  # Minimum % of hosts that must be reachable for WARNING (below this = CRITICAL)
    )
    timeout = 5.0

    def __init__(
        self,
        hosts: list[str] | None = None,
        ping_count: int = 1,
        **kwargs,
    ) -> None:
        """
        Initialize network checker.

        Args:
            hosts: List of hosts to ping (default: ["8.8.8.8", "1.1.1.1"]).
            ping_count: Number of ping packets to send per host.
            **kwargs: Additional BaseChecker arguments.
        """
        super().__init__(**kwargs)
        self.hosts = hosts or ["8.8.8.8", "1.1.1.1"]
        self.ping_count = ping_count

    def _ping_host(self, host: str) -> tuple[bool, float | None]:
        """
        Ping a single host.

        Args:
            host: Hostname or IP address to ping.

        Returns:
            Tuple of (success: bool, latency_ms: float | None).
        """
        # Build ping command based on platform
        if sys.platform == "win32":
            cmd = ["ping", "-n", str(self.ping_count), "-w", str(int(self.timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", str(self.ping_count), "-W", str(int(self.timeout)), host]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout * self.ping_count + 1,
            )

            if result.returncode == 0:
                # Try to parse latency from output
                latency = self._parse_latency(result.stdout)
                return True, latency
            return False, None

        except subprocess.TimeoutExpired:
            return False, None
        except Exception:
            return False, None

    def _parse_latency(self, output: str) -> float | None:
        """
        Parse average latency from ping output.

        Args:
            output: Ping command stdout.

        Returns:
            Average latency in ms, or None if parsing fails.
        """
        try:
            # macOS/Linux: "round-trip min/avg/max/stddev = 1.234/5.678/9.012/1.234 ms"
            if "avg" in output.lower():
                for line in output.split("\n"):
                    if "avg" in line.lower() or "average" in line.lower():
                        # Extract numbers
                        parts = line.split("=")[-1].strip().split("/")
                        if len(parts) >= 2:
                            return float(parts[1])
            # Windows: "Average = 5ms"
            if "average" in output.lower():
                for line in output.split("\n"):
                    if "average" in line.lower():
                        parts = line.split("=")
                        if len(parts) >= 2:
                            return float(parts[-1].strip().replace("ms", ""))
        except (ValueError, IndexError):
            pass
        return None

    def check(self) -> CheckResult:
        """
        Check network connectivity to all configured hosts.

        Returns:
            CheckResult with ping results for each host.
        """
        try:
            results = {}
            reachable_count = 0

            for host in self.hosts:
                success, latency = self._ping_host(host)
                results[host] = {
                    "reachable": success,
                    "latency_ms": latency,
                }
                if success:
                    reachable_count += 1

            total_hosts = len(self.hosts)
            reachable_percent = (reachable_count / total_hosts * 100) if total_hosts > 0 else 0

            metrics = {
                "hosts": results,
                "reachable_count": reachable_count,
                "total_hosts": total_hosts,
                "reachable_percent": reachable_percent,
            }

            # Determine status (inverted logic: higher % is better)
            if reachable_percent >= self.warning_threshold:
                status = CheckStatus.OK
            elif reachable_percent >= self.critical_threshold:
                status = CheckStatus.WARNING
            else:
                status = CheckStatus.CRITICAL

            message = f"Network: {reachable_count}/{total_hosts} hosts reachable ({reachable_percent:.0f}%)"

            return self._make_result(status=status, message=message, metrics=metrics)

        except Exception as e:
            return self._error_result(str(e))
