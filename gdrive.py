"""
Загрузка фото чеков в Google Drive через тот же сервисный аккаунт, что
используется для таблиц (gsheets.py) — чтобы ссылка на чек была видна
прямо в таблице «Расходы».

Нужна папка на Google Диске, расшаренная на email сервисного аккаунта
с правом редактора (точно так же, как расшариваются таблицы), и её ID
в переменной окружения DRIVE_RECEIPTS_FOLDER_ID.

Чтобы владелец таблиц (реальный человек, не сервисный аккаунт) тоже мог
открыть файл по ссылке, после загрузки файл явно расшаривается на
DRIVE_SHARE_EMAIL с правом чтения.
"""
import io
import os

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

_drive_service = None


def _get_service():
    global _drive_service
    if _drive_service is None:
        creds_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
        creds = Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _drive_service


def upload_receipt(data: bytes, filename: str, mime_type: str) -> str:
    """Загружает файл в папку DRIVE_RECEIPTS_FOLDER_ID и возвращает
    ссылку на просмотр (webViewLink). Расшаривает файл на владельца
    (DRIVE_SHARE_EMAIL), если эта переменная окружения задана."""
    service = _get_service()
    folder_id = os.environ["DRIVE_RECEIPTS_FOLDER_ID"]

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type or "application/octet-stream", resumable=False)
    file = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    share_email = os.environ.get("DRIVE_SHARE_EMAIL")
    if share_email:
        service.permissions().create(
            fileId=file["id"],
            body={"type": "user", "role": "reader", "emailAddress": share_email},
            sendNotificationEmail=False,
        ).execute()

    return file["webViewLink"]
