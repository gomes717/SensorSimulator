"""Read CSV file sizes from the Big IDEAs zip and write a summary CSV."""
import csv
import zipfile
from pathlib import Path

ZIP_PATH = Path(__file__).parent / "big-ideas-lab-glycemic-variability-and-wearable-device-data-1.1.3.zip"
OUTPUT = Path(__file__).parent / "dataset_sizes.csv"


def main() -> None:
    """Extract file sizes from the zip and write user_id x file_type CSV."""
    sizes: dict[str, dict[str, int]] = {}
    file_types: set[str] = set()

    with zipfile.ZipFile(ZIP_PATH) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if not name.endswith(".csv"):
                continue
            # e.g. "Food_Log_013.csv" -> file_type="Food_Log", user_id="013"
            stem = name[:-4]
            parts = stem.rsplit("_", 1)
            if len(parts) != 2:
                continue
            file_type, user_id = parts
            file_types.add(file_type)
            sizes.setdefault(user_id, {})[file_type] = round(info.file_size / 1024, 2)

    columns = sorted(file_types)
    rows = sorted(sizes)

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id"] + [f"{col} (KB)" for col in columns])
        for user_id in rows:
            writer.writerow([user_id] + [sizes[user_id].get(col, "") for col in columns])

    print(f"Written {len(rows)} users x {len(columns)} file types -> {OUTPUT}")


if __name__ == "__main__":
    main()
