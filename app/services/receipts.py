"""Generate and deliver receipts (saved locally + optional email/SMS + print).

The receipt is always written to data/receipts/<order_ref>.html. Email is sent
via SMTP when EMAIL_ENABLED=true; SMS is behind a driver that stays off until a
provider (Twilio/MSG91) is configured. When ``PRINTER_AUTO_PRINT_RECEIPTS`` is
true the receipt is also printed on the connected thermal printer.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.models import Order

RECEIPT_DIR = os.path.join("data", "receipts")


def render_html(order: Order) -> str:
    rows = "".join(
        f"""
        <tr>
          <td>{i.name}</td>
          <td style="text-align:center">{i.qty}</td>
          <td style="text-align:right">Rs. {float(i.unit_price):.2f}</td>
          <td style="text-align:right">Rs. {float(i.line_total):.2f}</td>
        </tr>"""
        for i in order.items
    )
    paid_at = order.paid_at.strftime("%d %b %Y, %I:%M %p") if order.paid_at else "-"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Receipt {order.order_ref}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;color:#1e293b;max-width:520px;margin:24px auto">
  <h2 style="color:#0b5cff;margin-bottom:0">{settings.store_name}</h2>
  <p style="color:#64748b;margin-top:4px">{settings.store_tagline}</p>
  <hr style="border:none;border-top:1px solid #e2e8f0">
  <p><b>Receipt</b>: {order.order_ref}<br>
     <b>Date</b>: {paid_at}<br>
     <b>Mobile</b>: {order.mobile}<br>
     <b>Email</b>: {order.email}<br>
     <b>Payment</b>: {order.payment_provider.upper()} ({order.payment_ref})</p>
  <table style="width:100%;border-collapse:collapse" cellpadding="8">
    <thead>
      <tr style="background:#f1f5f9;text-align:left">
        <th>Item</th><th style="text-align:center">Qty</th>
        <th style="text-align:right">Price</th><th style="text-align:right">Total</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <hr style="border:none;border-top:1px solid #e2e8f0">
  <p style="text-align:right">
    Subtotal: Rs. {float(order.subtotal):.2f}<br>
    Tax: Rs. {float(order.tax):.2f}<br>
    <b style="font-size:18px;color:#0b5cff">Total: Rs. {float(order.total):.2f}</b>
  </p>
  <p style="color:#64748b;font-size:12px;text-align:center;margin-top:24px">
    Thank you for shopping with {settings.store_name}. Show this receipt at the exit.
  </p>
</body></html>"""


def save_local(order: Order) -> str:
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    path = os.path.join(RECEIPT_DIR, f"{order.order_ref}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html(order))
    return path


def _send_email(order: Order) -> bool:
    if not settings.email_enabled or not order.email:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Your {settings.store_name} receipt - {order.order_ref}"
        msg["From"] = settings.email_from
        msg["To"] = order.email
        msg.set_content(
            f"Thanks for shopping at {settings.store_name}! "
            f"Your total was Rs. {float(order.total):.2f}. Receipt: {order.order_ref}."
        )
        msg.add_alternative(render_html(order), subtype="html")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as srv:
            srv.starttls()
            srv.login(settings.smtp_user, settings.smtp_password)
            srv.send_message(msg)
        return True
    except Exception as exc:
        print(f"[receipt] email failed: {exc}")
        return False


def _send_sms(order: Order) -> bool:
    if not settings.sms_enabled or not order.mobile:
        return False
    # Driver intentionally off until an SMS provider (Twilio/MSG91 + DLT) is set.
    print(f"[receipt] SMS provider not configured; skipping SMS to {order.mobile}")
    return False


def _print_receipt(order: Order) -> bool:
    if not getattr(settings, "printer_auto_print_receipts", False):
        return False
    try:
        from app.hardware.printer import get_printer
        printer = get_printer()
        printer.print_receipt(order)
        return True
    except Exception as exc:
        print(f"[receipt] print failed: {exc}")
        return False


def deliver(order: Order) -> dict:
    """Save locally + attempt email/SMS/print. Returns what succeeded."""
    path = save_local(order)
    emailed = _send_email(order)
    texted = _send_sms(order)
    printed = _print_receipt(order)
    return {"local_path": path, "emailed": emailed, "sms": texted, "printed": printed}
