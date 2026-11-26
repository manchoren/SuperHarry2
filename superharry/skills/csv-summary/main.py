import csv
import io
import json
import signal
import sys


MAX_INPUT = 256 * 1024
MAX_COLUMNS = 64
MAX_OUTPUT = 16 * 1024


def summarize(payload):
    if len(payload) > MAX_INPUT:
        raise ValueError("Input is too large.")
    rows = csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    header = next(rows, [])
    if not 1 <= len(header) <= MAX_COLUMNS or not all(item.strip() for item in header):
        raise ValueError("A nonempty header is required.")
    columns = [{"name": name.strip()[:48], "empty": 0} for name in header]
    row_count = 0
    for row in rows:
        if len(row) != len(columns):
            raise ValueError("A CSV row has a different width than the header.")
        row_count += 1
        for cell, column in zip(row, columns):
            if not cell.strip():
                column["empty"] += 1
    return {"rows": row_count, "columns": columns}


if __name__ == "__main__":
    signal.alarm(12)
    try:
        output = json.dumps(
            summarize(sys.stdin.buffer.read(MAX_INPUT + 1)), ensure_ascii=False
        ).encode("utf-8")
        if len(output) > MAX_OUTPUT:
            raise ValueError("Summary exceeds the output limit.")
        sys.stdout.buffer.write(output)
    except (ValueError, csv.Error):
        sys.exit(1)
