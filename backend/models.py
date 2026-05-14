from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from .database import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), unique=True, index=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    google_token = relationship("GoogleToken", back_populates="user", uselist=False, cascade="all, delete-orphan")
    submissions = relationship("FormSubmission", back_populates="user", cascade="all, delete-orphan")


class GoogleToken(Base):
    __tablename__ = "google_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    # Stored encrypted via Fernet
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    google_email = Column(String(320), nullable=True)
    scopes = Column(Text, nullable=True)  # JSON list of granted scopes

    user = relationship("User", back_populates="google_token")


class FormSubmission(Base):
    """Tracks the full lifecycle of one form-filling job."""
    __tablename__ = "form_submissions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    form_url = Column(Text, nullable=False)

    # Lifecycle status:
    # pending → validating → scraping → personal_info_needed →
    # llm_voting → filling → completed | failed
    status = Column(String(32), default="pending", nullable=False)

    form_type = Column(String(32), nullable=True)        # quiz / survey / feedback
    questions_json = Column(Text, nullable=True)          # all scraped questions (JSON)
    personal_info_questions_json = Column(Text, nullable=True)  # subset flagged as personal
    user_personal_info_json = Column(Text, nullable=True) # values provided by user
    answers_json = Column(Text, nullable=True)            # final answers dict
    result_json = Column(Text, nullable=True)             # per-question council results
    error_message = Column(Text, nullable=True)
    has_file_upload = Column(Boolean, default=False)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="submissions")


class ErrorLog(Base):
    """Persists all errors for debugging via the dashboard."""
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    submission_id = Column(Integer, nullable=True)
    error_type = Column(String(64), nullable=False)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    form_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
