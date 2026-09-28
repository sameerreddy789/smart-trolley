"""Interactive test script for Gemini Vision in Smart Trolley.

Usage:
  # Test with webcam snapshot:
  python scripts/test_vision.py --item "Lay's Classic 52g"

  # Test with a specific image file:
  python scripts/test_vision.py --item "Lay's Classic 52g" --image path/to/photo.jpg

  # Quick connection test:
  python scripts/test_vision.py --test-connection
"""
import argparse
import os
import sys

# Ensure root project directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.services import vision
from app.hardware.scanner import capture_camera_frame


def main():
    parser = argparse.ArgumentParser(description="Test Smart Trolley Gemini Vision Verification")
    parser.add_argument("--item", type=str, default="Lay's Classic 52g",
                        help="Product name to verify against (default: 'Lay's Classic 52g')")
    parser.add_argument("--image", type=str, default="",
                        help="Path to an existing image file (optional, otherwise webcam is used)")
    parser.add_argument("--test-connection", action="store_true",
                        help="Test API connection and exit")
    args = parser.parse_args()

    print("=" * 60)
    print("  SMART TROLLEY - GEMINI VISION TEST")
    print("=" * 60)
    print(f"Model: {settings.gemini_model}")
    masked_key = settings.gemini_api_key[:6] + "..." + settings.gemini_api_key[-4:] if settings.gemini_api_key else "(none)"
    print(f"API Key: {masked_key}")

    if not settings.gemini_api_key:
        print("\n[ERROR] GEMINI_API_KEY is not set in .env! Please add it and try again.")
        sys.exit(1)

    # 1. Image acquisition
    image_bytes = None
    if args.image:
        if not os.path.exists(args.image):
            print(f"\n[ERROR] Image file not found: {args.image}")
            sys.exit(1)
        with open(args.image, "rb") as fh:
            image_bytes = fh.read()
        print(f"\n[1/2] Loaded image from file: {args.image} ({len(image_bytes)} bytes)")
    else:
        print("\n[1/2] Capturing frame from webcam/camera (index: {})...".format(settings.camera_index))
        image_bytes = capture_camera_frame()
        if not image_bytes:
            print("[WARN] No live camera detected. Using a synthetic test card frame.")
            # Generate a simple blank/test card
            try:
                import cv2
                import numpy as np
                canvas = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(canvas, f"Product: {args.item}", (50, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                _, buf = cv2.imencode(".jpg", canvas)
                image_bytes = buf.tobytes()
            except Exception:
                # 1x1 fallback
                image_bytes = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'

    # 2. Run vision verification
    print(f"[2/2] Sending frame to {settings.gemini_model} for item verification: '{args.item}'...")
    res = vision.verify_item_in_image(image_bytes, args.item)

    print("\n" + "-" * 60)
    print("  VERIFICATION RESULT")
    print("-" * 60)
    print(f"  Verified / Matched : {'YES [PASS]' if res.get('verified') else 'NO [MISMATCH]'}")
    print(f"  Confidence Score   : {res.get('confidence', 0.0) * 100:.1f}%")
    print(f"  Detected Items     : {', '.join(res.get('detected_items', [])) or 'None'}")
    print(f"  Reasoning          : {res.get('reason')}")
    print(f"  Bypassed           : {res.get('bypassed', False)}")
    print("-" * 60)

    if res.get('verified'):
        print("\n>>> SUCCESS: Vision system confirmed the item match! <<<\n")
    else:
        print("\n>>> ALERT: Visual mismatch detected. Trolley will flag this scan! <<<\n")


if __name__ == "__main__":
    main()
