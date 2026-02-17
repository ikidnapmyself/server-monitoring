# CPU Multi-Sample Measurement Design

## Goal

Replace the CPU checker's single-snapshot measurement with multi-sample averaging to provide a meaningful baseline instead of a point-in-time reading.

## Problem

The current `CPUChecker` calls `psutil.cpu_percent(interval=1.0)` once per check. This single 1-second snapshot doesn't distinguish sustained load from momentary spikes, making it unreliable for alerting and baseline comparison.

## Approach

Take N discrete CPU samples at a fixed interval within a single `check()` call. Compute average, min, and max from those samples. Use the **average** to determine status (OK/WARNING/CRITICAL).

## API

### Constructor

```python
# Before
CPUChecker(interval=1.0, per_cpu=False)

# After
CPUChecker(samples=5, sample_interval=1.0, per_cpu=False)
```

- `samples` (int, default 5): Number of CPU readings to take.
- `sample_interval` (float, default 1.0): Seconds between each reading.
- `per_cpu` (bool, default False): Per-core breakdown.
- `interval` parameter is removed entirely.
- Total execution time: `samples * sample_interval` seconds (5s default).

### Metrics Output

```python
{
    "cpu_percent": 62.3,       # average across samples (used for status)
    "cpu_min": 45.1,           # lowest sample
    "cpu_max": 78.9,           # highest sample
    "samples": 5,              # number of samples taken
    "cpu_count": 8,            # core count
    # per_cpu=True adds:
    "per_cpu_percent": [...]   # per-core averages across samples
}
```

`cpu_min` and `cpu_max` are always present regardless of sample count.

### Status Determination

Status is determined from `cpu_percent` (the average), using the same `_determine_status()` logic (warning >= 70%, critical >= 90%).

## Call Site Changes

| File | Change |
|------|--------|
| `apps/checkers/checkers/cpu.py` | Replace `interval` with `samples` + `sample_interval`, add sampling loop |
| `apps/checkers/management/commands/run_check.py` | Replace `--interval` flag with `--samples` and `--sample-interval` |
| `apps/checkers/_tests/checkers/test_cpu.py` | Update all tests for new params and metrics shape |
| `apps/checkers/README.md` | Update documentation |

## Error Handling

If `psutil.cpu_percent()` raises during any sample, the checker returns an UNKNOWN result immediately — same behavior as today. No partial-sample logic.
