"""Worker entry point.

The queue implementation is deliberately deferred until the integration contract
and idempotency rules are documented. This process proves the Compose boundary.
"""
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.documents import process_next_extraction
from app.models import LoginAttempt, PasskeyChallenge, PasswordResetToken, ServiceHeartbeat, utc_now

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Tallystead worker started; document extraction queue active.")
while True:
    try:
        with SessionLocal() as db:
            heartbeat = db.scalar(select(ServiceHeartbeat).where(ServiceHeartbeat.service_name == "worker"))
            if heartbeat is None:
                heartbeat = ServiceHeartbeat(service_name="worker", status="healthy", detail="Worker loop active")
                db.add(heartbeat)
            else:
                heartbeat.status = "healthy"
                heartbeat.detail = "Worker loop active"
                heartbeat.heartbeat_at = utc_now()
            db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < utc_now() - timedelta(hours=1)))
            db.execute(delete(PasswordResetToken).where(PasswordResetToken.expires_at < utc_now() - timedelta(days=1)))
            db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < utc_now() - timedelta(days=1)))
            db.commit()
            process_next_extraction(db)
    except Exception:
        logger.exception("Worker heartbeat failed")
    time.sleep(30)
