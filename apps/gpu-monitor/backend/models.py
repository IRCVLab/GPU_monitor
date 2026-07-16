from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index, JSON, func, text
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Server(Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)               # 표시 이름 (e.g. "Poseidon")
    host = Column(Text, nullable=False)               # IP or hostname
    port = Column(Integer, default=22)
    ssh_user = Column(Text, nullable=False)
    ssh_password = Column(Text)                       # Fernet 암호화
    ssh_private_key = Column(Text)                    # Fernet 암호화
    network = Column(Text, default="internal")        # "internal" | "external"
    display_order = Column(Integer, default=0)
    registered_by = Column(Text)                      # username (계정 없음)
    created_at = Column(DateTime, default=func.now())

    notes = relationship("Note", back_populates="server", cascade="all, delete-orphan")
    gpu_metrics = relationship("GpuMetric", back_populates="server", cascade="all, delete-orphan")


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    username = Column(Text, nullable=False)           # 메모 작성자
    content = Column(Text, nullable=False)
    display_name = Column(String(40), nullable=True)
    priority = Column(Text, nullable=False, default="normal", server_default=text("'normal'"))
    kind = Column(Text, nullable=False, default="memo", server_default=text("'memo'"))
    gpu_indices = Column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    server = relationship("Server", back_populates="notes")


class GpuMetric(Base):
    """60초 간격 히스토리 아카이브. 실시간 데이터는 메모리에서 처리."""
    __tablename__ = "gpu_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    gpu_index = Column(Integer, nullable=False)
    utilization = Column(Integer)                     # %
    memory_used = Column(Integer)                     # MB
    memory_total = Column(Integer)                    # MB
    temperature = Column(Integer)                     # °C
    power_draw = Column(Integer)                      # W
    active_users = Column(Text, default="[]")         # JSON array
    collected_at = Column(DateTime, default=func.now())

    server = relationship("Server", back_populates="gpu_metrics")


class SlackAlertLog(Base):
    """Slack 알림 스팸 방지용 쿨다운 기록."""
    __tablename__ = "slack_alert_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id"))
    event_type = Column(Text, nullable=False)         # "offline"|"recovery"|"degraded"|"gpu_full"
    sent_at = Column(DateTime, default=func.now())


class EventLog(Base):
    """서버 상태 및 GPU 프로세스 이벤트 기록."""
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True)
    server_name = Column(String, nullable=True)   # 서버 삭제 후에도 이름 보존
    event_type = Column(String, nullable=False)
    severity = Column(String, nullable=False)     # info | warning | critical
    message = Column(String, nullable=False)
    event_metadata = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        Index("ix_event_logs_server_created", "server_id", "created_at"),
    )
