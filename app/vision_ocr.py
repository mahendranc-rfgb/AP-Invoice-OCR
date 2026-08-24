"""Vision AI OCR Engine for Handwritten & Complex Payment Vouchers (Supports Local Ollama Vision & Cloud Multimodal APIs)."""

from __future__ import annotations

import base64
import json
import logging
import re
from io import BytesIO
from pathlib import Path
from PIL import Image
import requests

from app.settings import settings

logger = logging.getLogger(__name__)

VISION_PROMPT = """You are a strict JSON API endpoint for SAP Business One A/P Invoice & Payment Voucher Extraction.
Your ONLY output MUST be a single raw JSON object. DO NOT include any introductory text, markdown headers, bullet points, or explanations.

Analyze the uploaded document image (which may be a printed tax invoice, handwritten claim voucher, or receipt).

OUTPUT FORMAT:
{
  "supplier_name": "Exact Supplier or Vendor Name found on the document",
  "supplier_gstin": "GSTIN if present, or null",
  "invoice_number": "Invoice / Bill Number",
  "vendor_ref_no": "Vendor Ref No",
  "invoice_date": "Exact date as printed on the document (e.g. 28-03-2025, 2025/03/28)",
  "narration": "Narration or description of invoice",
  "currency": "INR",
  "cgst_rate": 0.0,
  "sgst_rate": 0.0,
  "igst_rate": 0.0,
  "tax_percentage": 0.0,
  "tax_code": "GST0",
  "lines": [
    {
      "line_number": 1,
      "description": "Item description or material name",
      "quantity": 1.0,
      "unit_price": 100.0,
      "line_total": 100.0,
      "tax_percentage": 0.0,
      "tax_amount": 0.0,
      "tax_code": "GST0"
    }
  ],
  "subtotal": 100.0,
  "tax_amount": 0.0,
  "grand_total": 100.0
}


CRITICAL RULES:
1. Start your response immediately with '{' and end with '}'.
2. Extract ALL printed and handwritten table rows as individual items in the "lines" list. DO NOT skip any lines, even if they look identical. Pay close attention to varying quantities.
3. Extract exact Supplier Name, GSTIN, Invoice Number, Invoice Date, and Line items.
4. IF THE DOCUMENT IS AN INTERNAL PAYMENT VOUCHER OR CLAIM FORM, output the Handwritten Employee Name as the "supplier_name".
5. IF THE DOCUMENT IS A STANDARD SUPPLIER INVOICE, output the Supplier/Vendor Name (the company that issued the invoice). The recipient/buyer is LLM APPLIANCES PRIVATE LIMITED. You MUST NOT use the buyer's name or GSTIN (33AAACL1900F1Z9). Look for the prominent company name billing the buyer and use its GSTIN.
6. TAXES: Carefully look for CGST, SGST, IGST percentages (e.g., 6%, 9%, 18%). DO NOT output 0.0 if a percentage is clearly written on the document. Combine CGST + SGST (e.g. 6 + 6 = 12) for the tax_percentage.
7. MATH ACCURACY: DO NOT hallucinate the tax_amount! The tax_amount should be mathematically sound based on the subtotal. If the subtotal is 3030.0, a 12% tax amount is ~363.60, NOT 9090.0!
8. HANDWRITING: This document may contain dense, cursive, or sloppy handwritten text. Pay extreme attention to handwritten amounts, dates, and names. If a handwritten word is illegible, make your best phonetic guess based on the context.
9. Output NOTHING EXCEPT THE RAW JSON OBJECT.
"""




class VisionOcrEngine:
    """Multimodal Vision AI Engine supporting Local Ollama Vision (llama3.2-vision / qwen2-vl) & Cloud APIs."""

    def __init__(self, provider: str = "ollama", base_url: str = "http://localhost:11434", model: str = "llama3.2-vision") -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _pil_to_base64(self, image: Image.Image) -> str:
        buffered = BytesIO()
        image.convert("RGB").save(buffered, format="JPEG", quality=95)
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    def extract_from_image(self, image: Image.Image) -> dict | None:
        """Process a single PIL image using Google Gemini, Local Ollama Vision, or OpenAI Vision API."""
        b64_image = self._pil_to_base64(image)
        import os

        # 1. Try NVIDIA NIM Llama 3.2 Vision API (if NVIDIA_API_KEY is configured in .env or VISION_PROVIDER=nvidia)
        nvidia_key = os.getenv("NVIDIA_API_KEY")
        if nvidia_key or self.provider in {"nvidia", "nvidia_nim"}:
            if nvidia_key:
                try:
                    url = "https://integrate.api.nvidia.com/v1/chat/completions"
                    headers = {"Authorization": f"Bearer {nvidia_key}", "Content-Type": "application/json"}
                    model_name = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")
                    payload = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": VISION_PROMPT},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                                    }
                                ]
                            }
                        ],
                        "max_tokens": 4000,
                        "temperature": 0.1
                    }

                    for attempt in range(3):
                        try:
                            response = requests.post(url, headers=headers, json=payload, timeout=120)
                            if response.status_code == 200:
                                resp_json = response.json()
                                content = resp_json["choices"][0]["message"]["content"]
                                parsed = self._parse_json_response(content)
                                if parsed:
                                    return parsed
                            elif response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                                logger.warning(f"NVIDIA API HTTP {response.status_code}. Retrying in 3 seconds (attempt {attempt + 2}/3)...")
                                import time
                                time.sleep(3)
                            else:
                                logger.warning(f"NVIDIA API HTTP {response.status_code}: {response.text}")
                                break
                        except requests.RequestException as req_err:
                            if attempt < 2:
                                import time
                                time.sleep(3)
                                continue
                            raise req_err
                except Exception as err:
                    logger.warning(f"NVIDIA NIM Vision API call failed: {err}")

        # 2. Try Google Gemini Vision API (if GEMINI_API_KEY is configured in .env)
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key or self.provider in {"gemini", "google"}:
            if gemini_key:
                gemini_models = ["gemini-1.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
                for model_name in gemini_models:
                    try:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
                        payload = {
                            "contents": [
                                {
                                    "parts": [
                                        {"text": VISION_PROMPT},
                                        {
                                            "inline_data": {
                                                "mime_type": "image/jpeg",
                                                "data": b64_image
                                            }
                                        }
                                    ]
                                }
                            ],
                            "generationConfig": {
                                "response_mime_type": "application/json"
                            }
                        }
                        response = requests.post(url, json=payload, timeout=120)
                        if response.status_code == 200:
                            resp_json = response.json()
                            candidates = resp_json.get("candidates", [])
                            if candidates and "content" in candidates[0]:
                                parts = candidates[0]["content"].get("parts", [])
                                if parts:
                                    return self._parse_json_response(parts[0].get("text", ""))
                        elif response.status_code == 429:
                            logger.warning(f"Gemini API model {model_name} returned 429 (Quota Exceeded). Trying fallback model...")
                            continue
                        else:
                            logger.warning(f"Gemini API model {model_name} returned HTTP {response.status_code}: {response.text}")
                    except Exception as err:
                        logger.warning(f"Google Gemini Vision API call for {model_name} failed: {err}")




        # 2. Try Local Ollama Vision API
        if self.provider in {"ollama", "local"}:
            try:
                url = f"{self.base_url}/api/generate"
                payload = {
                    "model": self.model,
                    "prompt": VISION_PROMPT,
                    "images": [b64_image],
                    "stream": False,
                    "format": "json"
                }
                response = requests.post(url, json=payload, timeout=120)
                if response.status_code == 200:
                    resp_json = response.json()
                    response_text = resp_json.get("response", "")
                    return self._parse_json_response(response_text)
                else:
                    logger.warning(f"Ollama Vision API returned HTTP {response.status_code}: {response.text}")
            except Exception as err:
                logger.warning(f"Local Ollama Vision call failed: {err}")

        # 3. Try OpenAI / OpenAI-Compatible Vision API (if OPENAI_API_KEY is configured in .env)
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
                payload = {
                    "model": "gpt-4o",
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": VISION_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                                }
                            ]
                        }
                    ],
                    "max_tokens": 2000
                }
                response = requests.post(url, headers=headers, json=payload, timeout=120)
                if response.status_code == 200:
                    resp_json = response.json()
                    content = resp_json["choices"][0]["message"]["content"]
                    return self._parse_json_response(content)
            except Exception as err:
                logger.warning(f"OpenAI Vision call failed: {err}")

        return None


    def _parse_json_response(self, text: str) -> dict | None:
        """Clean and parse JSON from model text output."""
        cleaned = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except Exception:
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except Exception:
                    pass
        return None


vision_engine = VisionOcrEngine(
    provider=getattr(settings, "vision_provider", "ollama"),
    base_url=getattr(settings, "ollama_base_url", "http://localhost:11434"),
    model=getattr(settings, "ollama_model", "llama3.2-vision"),
)
