"""Shared metrics rendering for the check_health and run_check commands."""


def write_metrics(stdout, metrics: dict, indent: str) -> None:
    """Render checker metrics to stdout with the given indent.

    Disk checkers' space_hogs / old_files / large_files lists are
    rendered with per-section subtotals, full output for the largest
    section when 2+ are non-empty, and a byte-accurate trailer on
    truncated sections so the printed values reconcile against the
    grand total.
    """
    # Disk analysis checkers: space_hogs, old_files, large_files, recommendations.
    # Display rule: when 2+ sections are non-empty, the section with the largest
    # subtotal is shown in full so the user can see where the weight is.
    # Other sections (and the single-section case) keep the 10-item cap with a
    # trailer that includes the omitted byte weight, so the printed values
    # always reconcile against the grand total.
    sections = []
    for key in ("space_hogs", "old_files", "large_files"):
        items = metrics.get(key)
        if items:
            subtotal = sum(item["size_mb"] for item in items)
            sections.append((key, items, subtotal))

    largest_key = None
    if len(sections) >= 2:
        largest_key = max(sections, key=lambda s: s[2])[0]

    cap = 10
    for key, items, subtotal in sections:
        label = key.replace("_", " ").title()
        show_all = key == largest_key
        shown = items if show_all else items[:cap]
        count_note = "all shown" if show_all or len(items) <= cap else f"top {cap} shown"
        stdout.write(f"{indent}{label}: {subtotal:.1f} MB ({len(items)} items, {count_note})")
        for item in shown:
            size = f"{item['size_mb']:.1f} MB"
            extra = f" ({item['age_days']}d old)" if "age_days" in item else ""
            stdout.write(f"{indent}  - {item['path']}  {size}{extra}")
        if not show_all and len(items) > cap:
            omitted_weight = sum(it["size_mb"] for it in items[cap:])
            stdout.write(f"{indent}  ... and {len(items) - cap} more  ({omitted_weight:.1f} MB)")

    total = metrics.get("total_recoverable_mb")
    if total is not None:
        stdout.write(f"{indent}Total recoverable: {total:.1f} MB")

    recs = metrics.get("recommendations")
    if recs:
        stdout.write(f"{indent}Recommendations:")
        for rec in recs:
            stdout.write(f"{indent}  - {rec}")

    # Standard checkers: flat key-value pairs (percent, paths, etc.)
    skip = {
        "space_hogs",
        "old_files",
        "large_files",
        "total_recoverable_mb",
        "recommendations",
        "platform",
    }
    flat = {k: v for k, v in metrics.items() if k not in skip and not isinstance(v, (list, dict))}
    for key, value in flat.items():
        label = key.replace("_", " ")
        if isinstance(value, float):
            stdout.write(f"{indent}{label}: {value:.1f}")
        else:
            stdout.write(f"{indent}{label}: {value}")

    # Nested dicts (e.g. disk checker's per-path breakdown)
    nested = {k: v for k, v in metrics.items() if k not in skip and isinstance(v, dict)}
    for key, sub in nested.items():
        stdout.write(f"{indent}{key}:")
        for sub_key, sub_val in sub.items():
            if isinstance(sub_val, dict):
                parts = ", ".join(f"{k}: {v}" for k, v in sub_val.items())
                stdout.write(f"{indent}  {sub_key}: {parts}")
            elif isinstance(sub_val, float):
                stdout.write(f"{indent}  {sub_key}: {sub_val:.1f}")
            else:
                stdout.write(f"{indent}  {sub_key}: {sub_val}")
