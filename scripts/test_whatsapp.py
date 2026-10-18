"""Check your WhatsApp credentials by sending one message.
Usage:  python scripts/test_whatsapp.py 85291234567
(The number must be in your Meta test recipient list.)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import whatsapp
from app.config import S

if len(sys.argv) < 2:
    sys.exit("Usage: python scripts/test_whatsapp.py <number without +>")
if not whatsapp.enabled():
    sys.exit("WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID missing in .env")
print("Using Graph API", S.GRAPH_API_VERSION, "phone id", S.WHATSAPP_PHONE_NUMBER_ID)
r = whatsapp.send_text(sys.argv[1].lstrip("+"), "AI GrandKid 測試訊息 ✅ 收到即係設定成功！")
print("SUCCESS:" if r else "FAILED (see error above):", r)
print("Note: free-form messages only arrive if that phone messaged the bot in the last 24h. "
      "If it fails with 131047, send 'hi' from that phone to the test number first.")
