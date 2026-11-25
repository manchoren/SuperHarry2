"""Conservative project policy for Docker sandbox runtime components."""
import re


MINIMUMS = {"runc": (1, 4, 3), "containerd": (2, 3, 6)}


def check_component_versions(versions):
    """Reject unknown, unsupported, and explicitly affected releases."""
    rejected = []
    for component, minimum in MINIMUMS.items():
        reported = versions.get(component, "")
        if not isinstance(reported, str):
            reported = ""
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:\+[\w.-]+)?", reported)
        parsed = tuple(map(int, match.groups())) if match else None
        if component == "containerd" and parsed == (2, 4, 0):
            rejected.append(f"containerd {reported} (excluded: CVE-2026-53493)")
        elif parsed is None or parsed < minimum:
            rejected.append(
                f"{component} {reported or 'unknown'} (project minimum {'.'.join(map(str, minimum))})"
            )
    if rejected:
        raise ValueError("; ".join(rejected))
