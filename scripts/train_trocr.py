"""PyTorch TrOCR Fine-Tuning Script for Handwritten Invoices & Vouchers.

Loads line images and text transcriptions from data/trocr_dataset/metadata.csv,
fine-tunes microsoft/trocr-base-handwritten, evaluates CER/WER, and saves output model weights.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
from PIL import Image
import torch
from torch.utils.data import Dataset, DataLoader

try:
    from transformers import (
        TrOCRProcessor,
        VisionEncoderDecoderModel,
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
        default_data_collator,
    )
except ImportError:
    print("[-] Hugging Face transformers not installed. Install with: pip install transformers datasets jiwer torch")


class InvoiceHandwritingDataset(Dataset):
    """PyTorch Dataset for TrOCR line images and labels."""

    def __init__(self, root_dir: Path, df: pd.DataFrame, processor: TrOCRProcessor, max_target_length: int = 128):
        self.root_dir = root_dir
        self.df = df.reset_index(drop=True)
        self.processor = processor
        self.max_target_length = max_target_length

    def __len__(self) -> int:
        return len(self.df)

    def __getitem__(self, idx: int) -> dict:
        row = self.df.iloc[idx]
        image_path = self.root_dir / row["file_name"]
        text = str(row["text"])

        image = Image.open(image_path).convert("RGB")
        pixel_values = self.processor(image, return_tensors="pt").pixel_values.squeeze(0)

        labels = self.processor.tokenizer(
            text,
            padding="max_length",
            max_length=self.max_target_length,
            truncation=True,
            return_tensors="pt",
        ).input_ids.squeeze(0)

        # Replace padding token id with -100 to ignore in cross-entropy loss
        labels[labels == self.processor.tokenizer.pad_token_id] = -100

        return {"pixel_values": pixel_values, "labels": labels}


def compute_metrics(eval_pred, processor, cer_metric=None):
    """Calculate Character Error Rate (CER) during evaluation."""
    labels_ids = eval_pred.label_ids
    pred_ids = eval_pred.predictions

    pred_str = processor.batch_decode(pred_ids, skip_special_tokens=True)
    labels_ids[labels_ids == -100] = processor.tokenizer.pad_token_id
    label_str = processor.batch_decode(labels_ids, skip_special_tokens=True)

    if cer_metric is not None:
        cer = cer_metric.compute(predictions=pred_str, references=label_str)
        return {"cer": cer}
    return {}


def train_trocr(
    dataset_dir: Path,
    output_model_dir: Path,
    base_model_name: str = "microsoft/trocr-base-handwritten",
    num_epochs: int = 5,
    batch_size: int = 4,
    learning_rate: float = 5e-5,
    dry_run: bool = False,
):
    print(f"[*] Loading base model: {base_model_name}")
    processor = TrOCRProcessor.from_pretrained(base_model_name)
    model = VisionEncoderDecoderModel.from_pretrained(base_model_name)

    # Set decoder special tokens for generation
    model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.vocab_size = model.config.decoder.vocab_size

    manifest_path = dataset_dir / "metadata.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Dataset manifest not found at {manifest_path}. Run prepare_trocr_dataset.py first.")

    df = pd.read_csv(manifest_path)
    print(f"[+] Loaded dataset with {len(df)} samples.")

    if len(df) == 0:
        raise ValueError("Dataset is empty!")

    # Train / Val Split (90% train, 10% val)
    val_size = max(1, int(len(df) * 0.1))
    train_df = df.iloc[:-val_size] if len(df) > 1 else df
    val_df = df.iloc[-val_size:]

    train_dataset = InvoiceHandwritingDataset(dataset_dir, train_df, processor)
    val_dataset = InvoiceHandwritingDataset(dataset_dir, val_df, processor)

    if dry_run:
        print("[+] Dry run mode: verifying dataset loading and single forward pass...")
        sample = train_dataset[0]
        pixel_values = sample["pixel_values"].unsqueeze(0)
        labels = sample["labels"].unsqueeze(0)
        outputs = model(pixel_values=pixel_values, labels=labels)
        print(f"[+] Dry run success! Loss: {outputs.loss.item():.4f}")
        return

    training_args = Seq2SeqTrainingArguments(
        output_dir=str(output_model_dir),
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=learning_rate,
        num_train_epochs=num_epochs,
        predict_with_generate=True,
        logging_steps=10,
        save_strategy="epoch",
        evaluation_strategy="epoch",
        fp16=torch.cuda.is_available(),
        save_total_limit=2,
        report_to="none",
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        tokenizer=processor.feature_extractor,
        data_collator=default_data_collator,
    )

    print("[*] Starting PyTorch TrOCR fine-tuning...")
    trainer.train()

    print(f"[*] Saving fine-tuned model and processor to {output_model_dir}...")
    model.save_pretrained(output_model_dir)
    processor.save_pretrained(output_model_dir)
    print("[+] Fine-tuning complete!")


def main():
    parser = argparse.ArgumentParser(description="Fine-tune TrOCR on handwritten invoice dataset")
    parser.add_argument("--dataset-dir", type=str, default="data/trocr_dataset", help="Path to prepared dataset")
    parser.add_argument("--output-dir", type=str, default="data/models/trocr_handwritten", help="Output model path")
    parser.add_argument("--base-model", type=str, default="microsoft/trocr-base-handwritten", help="Pretrained model")
    parser.add_argument("--epochs", type=int, default=5, help="Number of training epochs")
    parser.add_argument("--batch-size", type=int, default=4, help="Batch size per GPU/CPU")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate")
    parser.add_argument("--dry-run", action="store_true", help="Run model setup check without full training")

    args = parser.parse_args()

    train_trocr(
        dataset_dir=Path(args.dataset_dir),
        output_model_dir=Path(args.output_dir),
        base_model_name=args.base_model,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
