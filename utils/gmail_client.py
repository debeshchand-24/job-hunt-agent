import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from loguru import logger

from auth.google_auth import get_credentials
from config.settings import GMAIL_RECIPIENT


class GmailClient:
    def __init__(self):
        creds = get_credentials()
        self._service   = build("gmail", "v1", credentials=creds)
        self._messages  = self._service.users().messages()
        self.recipient  = GMAIL_RECIPIENT

    def send_email(self, subject: str, body: str):
        msg = MIMEText(body, "plain", "utf-8")
        msg["to"]      = self.recipient
        msg["from"]    = "me"
        msg["subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        self._messages.send(userId="me", body={"raw": raw}).execute()
        logger.info(f"Email sent: {subject}")
