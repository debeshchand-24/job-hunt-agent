from googleapiclient.discovery import build
from auth.google_auth import get_credentials


class GmailClient:
    def __init__(self):
        creds = get_credentials()
        self._service = build("gmail", "v1", credentials=creds)
        self._messages = self._service.users().messages()
