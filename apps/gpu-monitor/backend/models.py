from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, Integer, String, Text, DateTime, ForeignKey, Index, JSON, Float, UniqueConstraint, func, text
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
    network_metrics = relationship("NetworkMetric", back_populates="server", cascade="all, delete-orphan")


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


class ExperimentNoteSettings(Base):
    __tablename__ = "experiment_note_settings"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False)


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

    __table_args__ = (Index("ix_gpu_metrics_collected_at", "collected_at"),)


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


class HardwareRound(Base):
    """One real hardware polling round; expected coverage is frozen at collection."""
    __tablename__ = "hardware_rounds"
    id = Column(Integer, primary_key=True)
    collected_at = Column(DateTime, nullable=False, index=True)


class HardwarePowerObservation(Base):
    __tablename__ = "hardware_power_observations"
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("hardware_rounds.id"), nullable=False, index=True)
    server_id = Column(Integer, nullable=False)
    network = Column(Text, nullable=False)
    power_watts = Column(Float, nullable=True)
    observed_at = Column(DateTime, nullable=True)


class HardwareImport(Base):
    """Permanent import marker, retained after old observations expire."""
    __tablename__ = "hardware_imports"
    key = Column(Text, primary_key=True)


class PowerRawSample(Base):
    """Measured source-specific series; nullable samples explicitly interrupt energy."""
    __tablename__ = 'power_raw_samples'
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey('hardware_rounds.id'), nullable=False, index=True)
    scope = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    observed_at = Column(DateTime, nullable=False)
    power_watts = Column(Float)
    cohort = Column(Text, nullable=False)
    expected_count = Column(Integer, nullable=False)
    server_count = Column(Integer, nullable=False)
    interval_seconds = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('round_id','scope','source'), Index('ix_power_series_time','scope','source','observed_at'), Index('ix_power_raw_observed','observed_at'), Index('ix_power_raw_interval','interval_seconds'))


class PowerPsuReading(Base):
    __tablename__ = 'power_psu_readings'
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey('hardware_rounds.id'), nullable=False, index=True)
    server_id = Column(Integer, nullable=False)
    sensor_name = Column(Text, nullable=False)
    direction = Column(Text, nullable=False)
    power_watts = Column(Float)
    status = Column(Text)
    observed_at = Column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint('round_id','server_id','sensor_name','direction'), Index('ix_power_psu_observed','observed_at'))


class PowerPsuIdentity(Base):
    __tablename__ = 'power_psu_identities'
    server_id = Column(Integer, primary_key=True)
    sensor_name = Column(Text, primary_key=True)
    direction = Column(Text, primary_key=True)


class PowerRollup(Base):
    __tablename__ = 'power_rollups'
    id = Column(Integer, primary_key=True)
    scope = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    resolution = Column(Text, nullable=False)
    bucket_start = Column(DateTime, nullable=False)
    energy_wh = Column(Float, nullable=False)
    observed_seconds = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False)
    min_watts = Column(Float)
    max_watts = Column(Float)
    first_sample = Column(JSON)
    last_sample = Column(JSON)
    start_edge = Column(JSON)
    end_edge = Column(JSON)
    __table_args__ = (UniqueConstraint('scope','source','resolution','bucket_start'), Index('ix_power_rollup_query','scope','source','resolution','bucket_start'), Index('ix_power_rollup_retention','resolution','bucket_start'))


class PowerCheckpoint(Base):
    """Finalized rollup boundary plus one real anchor, separate from retained raw."""
    __tablename__ = 'power_checkpoints'
    scope = Column(Text, primary_key=True)
    source = Column(Text, primary_key=True)
    closed_before = Column(DateTime, nullable=False)
    anchor = Column(JSON)


class CpuTemperatureObservation(Base):
    """Individual CPU sensors from actual hardware polling rounds."""
    __tablename__ = 'cpu_temperature_observations'
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey('hardware_rounds.id'), nullable=False, index=True)
    server_id = Column(Integer, nullable=False)
    network = Column(Text, nullable=False)
    sensor_name = Column(Text, nullable=False)
    temperature = Column(Float, nullable=True)
    observed_at = Column(DateTime, nullable=False)
    interval_seconds = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('round_id','server_id','sensor_name'), Index('ix_cpu_temp_observed','observed_at'), Index('ix_cpu_temp_network_time','network','observed_at'))


class NetworkMetric(Base):
    """Actual physical-interface byte rates archived independently of GPU availability."""
    __tablename__ = 'network_metrics'
    server = relationship("Server", back_populates="network_metrics")
    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey('servers.id'), nullable=False)
    network_rx_bytes_per_second = Column(Float)
    network_tx_bytes_per_second = Column(Float)
    collected_at = Column(DateTime, nullable=False)
    interval_seconds = Column(Integer, nullable=False, default=60)
    __table_args__ = (Index('ix_network_server_time','server_id','collected_at'), Index('ix_network_collected_at','collected_at'))


class PowerFlowDiagram(Base):
    """Mutable pointer only; every saved graph lives in an immutable revision."""
    __tablename__ = 'power_flow_diagrams'
    id = Column(String(36), primary_key=True)
    name = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_scenario = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class PowerFlowRevision(Base):
    __tablename__ = 'power_flow_revisions'
    id = Column(Integer, primary_key=True)
    diagram_id = Column(String(36), ForeignKey('power_flow_diagrams.id'), nullable=False)
    version = Column(Integer, nullable=False)
    name = Column(Text, nullable=False)
    graph = Column(JSON, nullable=False)
    effective_from = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint('diagram_id', 'version', name='uq_power_flow_revision'),
        Index('ix_power_flow_revision_effective', 'diagram_id', 'effective_from'),
    )
