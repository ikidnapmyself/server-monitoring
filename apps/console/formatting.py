"""Display formatting shared by the console's read models."""


def humanize_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    if seconds < 1:
        return f"{min(999, round(seconds * 1000))}ms"
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m {whole % 60:02d}s"
    if whole < 86400:
        return f"{whole // 3600}h {whole % 3600 // 60:02d}m"
    return f"{whole // 86400}d {whole % 86400 // 3600}h"
