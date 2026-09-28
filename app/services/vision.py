"""AI Vision Verification service using Google Gemini Flash.

Level-2 security cross-check:
When an item is scanned and placed in the trolley basket, the camera captures
a snapshot. This service asks Gemini Flash to verify if the physical product
seen in the image matches the item that was scanned.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests

from app.config import settings

API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
)


def verify_item_in_image(
    image_bytes: bytes,
    expected_item_name: str,
    timeout_s: float = 12.0,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Verify whether expected_item_name matches what is visible in image_bytes.

    Returns a dict with:
      - verified: bool (True if matched or allowed by fallback)
      - confidence: float (0.0 to 1.0)
      - detected_items: list[str]
      - reason: str (human-readable explanation)
      - bypassed: bool (True if vision check was bypassed/fell back)
    """
    if not settings.vision_verification_enabled:
        return {
            "verified": True,
            "confidence": 1.0,
            "detected_items": [],
            "reason": "Vision verification disabled in configuration",
            "bypassed": True,
        }

    api_key = settings.gemini_api_key.strip()
    if not api_key:
        print("[vision] Warning: GEMINI_API_KEY is not set. Bypassing camera check.")
        return {
            "verified": True,
            "confidence": 1.0,
            "detected_items": [],
            "reason": "GEMINI_API_KEY not configured",
            "bypassed": True,
        }

    if not image_bytes:
        if settings.vision_fallback_on_error:
            print("[vision] No camera frame received; falling back to weight check.")
            return {
                "verified": True,
                "confidence": 0.0,
                "detected_items": [],
                "reason": "No camera frame captured (fallback allowed)",
                "bypassed": True,
            }
        return {
            "verified": False,
            "confidence": 0.0,
            "detected_items": [],
            "reason": "Camera image missing",
            "bypassed": False,
        }

    # Prepare base64 image data
    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    model_name = settings.gemini_model.strip() or "gemini-2.5-flash"
    url = API_URL_TEMPLATE.format(model=model_name, key=api_key)

    prompt = (
        f"You are the computer vision security system for a smart supermarket shopping trolley.\n"
        f"A shopper scanned the product: \"{expected_item_name}\" and placed an item into the trolley basket.\n"
        f"Analyze this camera frame of the basket.\n"
        f"Task:\n"
        f"1. Identify the products or objects visible in the image.\n"
        f"2. Determine whether the scanned product \"{expected_item_name}\" (or its brand/packaging/type) "
        f"is present in the basket.\n\n"
        f"Respond ONLY with a valid JSON object in this exact format:\n"
        f"{{\n"
        f"  \"matches_expected\": true or false,\n"
        f"  \"confidence\": float between 0.0 and 1.0,\n"
        f"  \"detected_items\": [\"item 1\", \"item 2\"],\n"
        f"  \"reason\": \"plain English explanation in 1 short sentence\"\n"
        f"}}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": b64_img,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "response_mime_type": "application/json",
        },
    }

    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.post(url, json=payload, timeout=timeout_s)
            if res.status_code == 200:
                data = res.json()
                raw_text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "{}")
                )
                parsed = json.loads(raw_text)
                matches = bool(parsed.get("matches_expected", False))
                confidence = float(parsed.get("confidence", 0.0))
                detected = parsed.get("detected_items", [])
                reason = parsed.get("reason", "Item visual analysis completed.")

                print(f"[vision] Scanned '{expected_item_name}' -> Matches: {matches} "
                      f"(Confidence: {confidence:.2f}, Reason: {reason})")

                return {
                    "verified": matches,
                    "confidence": confidence,
                    "detected_items": detected,
                    "reason": reason,
                    "bypassed": False,
                }

            if res.status_code in (503, 429):
                # Temporary server demand or rate limit spike; retry after short sleep
                last_error = f"API returned {res.status_code}: {res.text[:120]}"
                print(f"[vision] Attempt {attempt} failed ({last_error}), retrying...")
                time.sleep(1.5 * attempt)
                continue

            last_error = f"API HTTP {res.status_code}: {res.text[:150]}"
            break

        except Exception as exc:
            last_error = str(exc)
            print(f"[vision] Attempt {attempt} exception: {exc}")
            time.sleep(1.0)

    # If all attempts failed
    print(f"[vision] Verification failed after {max_retries} attempts: {last_error}")
    if settings.vision_fallback_on_error:
        print("[vision] Fallback enabled: allowing scan through with warning.")
        return {
            "verified": True,
            "confidence": 0.0,
            "detected_items": [],
            "reason": f"Vision API temporary issue ({last_error}) - passed on weight check",
            "bypassed": True,
        }

    return {
        "verified": False,
        "confidence": 0.0,
        "detected_items": [],
        "reason": f"Vision check failed: {last_error}",
        "bypassed": False,
    }
