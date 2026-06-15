import os
import tempfile
import time

from .constants import REPORT_FILENAME


def build_report_text(removed_items, errors):
    lines = [
        "PHANTOMLINK RAT REMOVAL REPORT",
        "=" * 60,
        "",
        f"Removal completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "SUCCESSFUL ACTIONS:",
    ]

    for item in removed_items:
        lines.append(f"- {item}")

    if errors:
        lines.extend(["", "ERRORS:"])
        for error in errors:
            lines.append(f"- {error}")

    return "\n".join(lines) + "\n"


def report_path():
    return os.path.join(tempfile.gettempdir(), REPORT_FILENAME)


def write_report(removed_items, errors):
    path = report_path()
    with open(path, "w", encoding="UTF-8") as report_file:
        report_file.write(build_report_text(removed_items, errors))
    return path
