from googleapiclient.discovery import build
from auth.google_auth import get_credentials


class DocsClient:
    def __init__(self):
        creds = get_credentials()
        self._service = build("docs", "v1", credentials=creds)
        self._documents = self._service.documents()
