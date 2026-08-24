"""Dataset Preparation Pipeline for TrOCR Fine-Tuning.

Generates Hugging Face / PyTorch dataset manifests (metadata.csv) mapping image crops
to ground-truth handwritten text transcriptions.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def setup_dataset_dirs(base_dir: Path) -> tuple[Path, Path]:
    """Create directory structure for dataset storage."""
    images_dir = base_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return base_dir, images_dir


def create_manifest(dataset_dir: Path, data_rows: list[tuple[str, str]]) -> Path:
    """Create metadata.csv with columns: file_name, text."""
    manifest_path = dataset_dir / "metadata.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_name", "text"])
        for file_name, text in data_rows:
            writer.writerow([file_name, text])
    print(f"[+] Dataset manifest written to {manifest_path} with {len(data_rows)} samples.")
    return manifest_path


def generate_sample_synthetic_data(dataset_dir: Path, num_samples: int = 10) -> Path:
    """Generate synthetic sample line images for testing the training pipeline dry-run."""
    dataset_dir, images_dir = setup_dataset_dirs(dataset_dir)
    data_rows = []
    
    sample_texts = [
        "TN11 BL 1095",
        "Mr. PRAVEEN Sir",
        "44856",
        "44760",
        "PAID",
        "REF / CHQ NO 001481",
        "DATE 29/03/25",
        "LLM APPLIANCES",
        "27800.00",
        "porter from HMI to LLM = 10 sets",
    ]

    for i in range(num_samples):
        text = sample_texts[i % len(sample_texts)]
        file_name = f"sample_{i+1:04d}.png"
        file_path = images_dir / file_name

        # Create a line image mimicking handwriting crop
        img = Image.new("RGB", (384, 64), color=(250, 248, 240))
        draw = ImageDraw.Draw(img)
        
        # Add text
        draw.text((20, 18), text, fill=(20, 30, 80))
        
        # Add slight artificial noise/line artifact
        draw.line([(5, 60), (375, 60)], fill=(200, 200, 200), width=1)

        img.save(file_path)
        data_rows.append((f"images/{file_name}", text))

    return create_manifest(dataset_dir, data_rows)


def extract_from_invoices_db(db_path: Path, dataset_dir: Path) -> Path:
    """Export human-corrected text fields from SQLite database into dataset format."""
    dataset_dir, images_dir = setup_dataset_dirs(dataset_dir)
    data_rows = []

    if not db_path.exists():
        print(f"[-] Database file not found at {db_path}.")
        return create_manifest(dataset_dir, data_rows)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT upload_id, filename, extracted_text, invoice_payload FROM uploads WHERE extraction_status = 'CORRECTED'"
        ).fetchall()

    for idx, (upload_id, filename, extracted_text, invoice_payload) in enumerate(rows):
        if not invoice_payload:
            continue
        try:
            payload = json.loads(invoice_payload)
            # Extract header and line fields as training strings
            text_snippets = []
            if payload.get("header"):
                hdr = payload["header"]
                if hdr.get("vendor_name"):
                    text_snippets.append(str(hdr["vendor_name"]))
                if hdr.get("invoice_number"):
                    text_snippets.append(str(hdr["invoice_number"]))
            
            for snippet in text_snippets:
                file_name = f"db_{idx}_{len(data_rows):04d}.png"
                # Placeholder image generation for DB records without exact line bounding boxes
                img = Image.new("RGB", (384, 64), color=(255, 255, 255))
                draw = ImageDraw.Draw(img)
                draw.text((10, 20), snippet, fill=(0, 0, 0))
                img.save(images_dir / file_name)
                data_rows.append((f"images/{file_name}", snippet))
        except Exception as err:
            print(f"[-] Failed to process payload for {upload_id}: {err}")

    return create_manifest(dataset_dir, data_rows)


def main():
    parser = argparse.ArgumentParser(description="Prepare dataset for TrOCR fine-tuning")
    parser.add_argument("--output-dir", type=str, default="data/trocr_dataset", help="Dataset directory")
    parser.add_argument("--db-path", type=str, default="data/invoices.db", help="Path to invoices SQLite DB")
    parser.add_argument("--create-samples", action="store_true", help="Generate synthetic samples for testing")

    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    if args.create_samples:
        print("[*] Generating synthetic sample dataset for TrOCR training pipeline verification...")
        generate_sample_synthetic_data(output_dir)
    else:
        print(f"[*] Extracting ground-truth samples from {args.db_path}...")
        extract_from_invoices_db(Path(args.db_path), output_dir)


if __name__ == "__main__":
    main()
