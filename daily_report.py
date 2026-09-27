"""Daily AdMob earnings + Meta (Facebook) Ads spend report -> Slack and/or email."""
import os, json, smtplib, datetime as dt
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TZ = "Europe/Istanbul"
DAYS_BACK = int(os.getenv("DAYS_BACK", "1"))  # 1 = yesterday (complete), 0 = today so far
DAY = dt.datetime.now(ZoneInfo(TZ)).date() - dt.timedelta(days=DAYS_BACK)
GRAPH = os.getenv("META_GRAPH_VERSION", "v21.0")  # bump to the current Graph API version


def admob_earnings(day: dt.date) -> float:
    creds = Credentials(
        None,
        refresh_token=os.environ["ADMOB_REFRESH_TOKEN"],
        client_id=os.environ["ADMOB_CLIENT_ID"],
        client_secret=os.environ["ADMOB_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/admob.readonly"],
    )
    creds.refresh(Request())
    d = {"year": day.year, "month": day.month, "day": day.day}
    body = {"reportSpec": {
        "dateRange": {"startDate": d, "endDate": d},
        "dimensions": ["DATE"],
        "metrics": ["ESTIMATED_EARNINGS"],
        "timeZone": TZ,  # remove if your account rejects it; account default is used then
        "localizationSettings": {"currencyCode": "USD"},
    }}
    r = requests.post(
        f"https://admob.googleapis.com/v1/accounts/{os.environ['ADMOB_PUBLISHER_ID']}/networkReport:generate",
        headers={"Authorization": f"Bearer {creds.token}"}, json=body, timeout=60)
    r.raise_for_status()
    micros = sum(int(x["row"]["metricValues"]["ESTIMATED_EARNINGS"]["microsValue"])
                 for x in r.json() if "row" in x)
    return micros / 1e6


def meta_spend(day: dt.date) -> float:
    total = 0.0
    for acct in os.environ["META_AD_ACCOUNT_IDS"].split(","):  # e.g. "123,456" (no act_ prefix)
        r = requests.get(
            f"https://graph.facebook.com/{GRAPH}/act_{acct.strip()}/insights",
            params={"fields": "spend", "level": "account",
                    "time_range": json.dumps({"since": day.isoformat(), "until": day.isoformat()}),
                    "access_token": os.environ["META_ACCESS_TOKEN"]},
            timeout=60)
        r.raise_for_status()
        data = r.json().get("data", [])
        total += float(data[0]["spend"]) if data else 0.0
    return total  # in the ad account's currency; keep it USD to match AdMob


def send_slack(text: str):
    if url := os.getenv("SLACK_WEBHOOK_URL"):
        requests.post(url, json={"text": text}, timeout=30).raise_for_status()


def send_email(subject: str, text: str):
    if not os.getenv("SMTP_USER"):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ["SMTP_USER"], os.environ["EMAIL_TO"]
    msg.set_content(text)
    with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), 465) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


if __name__ == "__main__":
    earn, spend = admob_earnings(DAY), meta_spend(DAY)
    net = earn - spend
    roas = f"{earn / spend:.2f}x" if spend else "n/a"
    label = "today so far" if DAYS_BACK == 0 else DAY.isoformat()
    text = (f"📊 {label} — AdMob: ${earn:,.2f} | Meta spend: ${spend:,.2f} | "
            f"Net: {'+' if net >= 0 else '-'}${abs(net):,.2f} (ROAS {roas})")
    print(text)
    send_slack(text)
    send_email(f"Daily ad report {label}", text)
