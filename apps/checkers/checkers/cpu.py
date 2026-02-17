"""
CPU usage checker with multi-sample averaging.
"""

import psutil

from apps.checkers.checkers.base import BaseChecker, CheckResult


class CPUChecker(BaseChecker):
    """
    Check CPU usage by averaging multiple samples.

    Takes N samples at a fixed interval and computes avg/min/max.
    Status is determined from the average.
    Default: 5 samples, 1 second apart (5 seconds total).
    Default thresholds: warning at 70%, critical at 90%.
    """

    name = "cpu"
    warning_threshold = 70.0
    critical_threshold = 90.0

    def __init__(
        self,
        samples: int = 5,
        sample_interval: float = 1.0,
        per_cpu: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.samples = samples
        self.sample_interval = sample_interval
        self.per_cpu = per_cpu

    def check(self) -> CheckResult:
        try:
            if self.per_cpu:
                return self._check_per_cpu()
            return self._check_system()
        except Exception as e:
            return self._error_result(str(e))

    def _check_system(self) -> CheckResult:
        readings = [psutil.cpu_percent(interval=self.sample_interval) for _ in range(self.samples)]
        avg = sum(readings) / len(readings)
        metrics = {
            "cpu_percent": round(avg, 1),
            "cpu_min": min(readings),
            "cpu_max": max(readings),
            "samples": self.samples,
            "cpu_count": psutil.cpu_count(),
        }
        status = self._determine_status(avg)
        message = f"CPU usage: {avg:.1f}% (avg of {self.samples} samples)"
        return self._make_result(status=status, message=message, metrics=metrics)

    def _check_per_cpu(self) -> CheckResult:
        all_samples = [
            psutil.cpu_percent(interval=self.sample_interval, percpu=True)
            for _ in range(self.samples)
        ]
        num_cores = len(all_samples[0])
        per_core_avgs = [
            round(sum(s[i] for s in all_samples) / self.samples, 1) for i in range(num_cores)
        ]
        per_sample_maxes = [max(s) for s in all_samples]
        cpu_percent = max(per_core_avgs)
        metrics = {
            "cpu_percent": cpu_percent,
            "cpu_min": min(per_sample_maxes),
            "cpu_max": max(per_sample_maxes),
            "samples": self.samples,
            "per_cpu_percent": per_core_avgs,
            "cpu_count": num_cores,
        }
        status = self._determine_status(cpu_percent)
        message = f"CPU usage: {cpu_percent:.1f}% (avg of {self.samples} samples, busiest core)"
        return self._make_result(status=status, message=message, metrics=metrics)
