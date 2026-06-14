"""Notification channels: Telegram (default), email (optional), console."""
import smtplib
import ssl
from email.mime.text import MIMEText
import requests


def _fmt_deal(d):
    visa = d["visa"]["label"]
    flag = "✅" if d["visa"]["easy"] else ("📝" if d["visa"]["status"] not in
                                          ("no_entry", "unknown") else "⚠️")
    dep = (d["departure_at"] or "")[:16].replace("T", " ")
    ret = (d["return_at"] or "")[:16].replace("T", " ")
    stops = "non-stop" if not d["transfers"] else f"{d['transfers']} stop(s)"
    line = (f"{d['city']} ({d['destination']}) · {d['country']}\n"
            f"  {int(round(d['price']))} {d['currency']}  |  {stops}\n"
            f"  Out {dep}  →  Back {ret}")
    bits = [b for b in (d.get("airline"), d.get("flight_number"), d.get("aircraft")) if b]
    if bits:
        line += "\n  ✈ " + "  ·  ".join(bits)
    if d.get("route"):
        line += f"\n  ↳ {d['route']}"
    line += f"\n  {flag} Visa: {visa}"
    if d.get("link"):
        line += f"\n  Book: {d['link']}"
    return line


def render(deals, header="Weekend flight deals from your home airport"):
    if not deals:
        return None
    body = [f"✈️  {header}", ""]
    for d in deals:
        body.append(_fmt_deal(d))
        body.append("")
    return "\n".join(body).rstrip()


class Notifier:
    def __init__(self, cfg):
        self.cfg = cfg

    def send(self, text):
        sent = False
        ch = self.cfg.get("notify", {})
        if ch.get("telegram", {}).get("enabled"):
            sent |= self._telegram(text, ch["telegram"])
        if ch.get("email", {}).get("enabled"):
            sent |= self._email(text, ch["email"])
        if ch.get("console", True) or not sent:
            print(text)
            sent = True
        return sent

    def _telegram(self, text, c):
        try:
            url = f"https://api.telegram.org/bot{c['bot_token']}/sendMessage"
            r = requests.post(url, timeout=20, data={
                "chat_id": c["chat_id"], "text": text,
                "disable_web_page_preview": True,
            })
            r.raise_for_status()
            return True
        except Exception as e:
            print(f"[telegram] failed: {e}")
            return False

    def _email(self, text, c):
        try:
            msg = MIMEText(text)
            msg["Subject"] = c.get("subject", "Weekend flight deals")
            msg["From"] = c["from_addr"]
            msg["To"] = c["to_addr"]
            ctx = ssl.create_default_context()
            with smtplib.SMTP(c["smtp_host"], c.get("smtp_port", 587)) as s:
                s.starttls(context=ctx)
                s.login(c["username"], c["password"])
                s.send_message(msg)
            return True
        except Exception as e:
            print(f"[email] failed: {e}")
            return False
