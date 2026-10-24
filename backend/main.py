"""Entry point kept for the systemd unit (`uvicorn main:app`). The app lives in app/."""

from app.main import app  # noqa: F401
