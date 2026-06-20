#!/usr/bin/python3

from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------- config


class ConfigPayload(BaseModel):
    """Full configuration document."""

    config: dict[str, Any]


class ValidateResponse(BaseModel):
    """Result of validating a configuration."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


class TestTarget(BaseModel):
    """A provider/channel connectivity test request."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    """Result of a live connectivity test."""

    ok: bool
    detail: str


class ModelDiscoveryResult(BaseModel):
    """The list of model identifiers a provider exposes."""

    ok: bool
    models: list[str] = Field(default_factory=list)
    detail: str


# -------------------------------------------------------------------- secrets


class SecretInfo(BaseModel):
    """A single managed secret (value included; masked client-side by default)."""

    key: str
    value: str
    backend: str


class SecretListResponse(BaseModel):
    """All managed secrets and the active backend."""

    backend: str
    secrets: list[SecretInfo] = Field(default_factory=list)


class SecretUpdate(BaseModel):
    """Set or change a single secret value."""

    value: str
    note: str = ""


class BitwardenTest(BaseModel):
    """A Bitwarden access-token verification request."""

    access_token: str
    organization_id: str | None = None
    project_id: str | None = None
    api_url: str | None = None
    identity_url: str | None = None


# --------------------------------------------------------------------- memory


class Conclusion(BaseModel):
    """A stored reasoning conclusion."""

    id: str
    content: str
    peer: str
    kind: str
    premises: list[str] = Field(default_factory=list)
    times_seen: int = 1
    ts: float = 0.0
    source: str = "derived"


class ConclusionUpdate(BaseModel):
    """A partial update to a conclusion."""

    content: str | None = None
    kind: str | None = None
    premises: list[str] | None = None


class ManualConclusion(BaseModel):
    """An operator-authored conclusion."""

    peer: str
    kind: str
    content: str
    premises: list[str] = Field(default_factory=list)


class PeerCard(BaseModel):
    """A peer card and its freeze state."""

    peer: str
    text: str
    max_chars: int
    frozen: bool


class PeerCardUpdate(BaseModel):
    """A peer-card text update."""

    text: str


class FreezeUpdate(BaseModel):
    """A peer-card freeze toggle."""

    frozen: bool


class PeerSummary(BaseModel):
    """A peer with its conclusion count and card length."""

    peer: str
    count: int
    card_chars: int
    frozen: bool


class ImportPayload(BaseModel):
    """A batch of conclusions to import."""

    records: list[dict[str, Any]] = Field(default_factory=list)


class DeriverState(BaseModel):
    """The deriver pause state."""

    paused: bool


# ------------------------------------------------------------------ lifecycle


class StatusResponse(BaseModel):
    """Overall agent and panel status."""

    version: str
    agent_alive: bool
    heartbeat: float | None
    deriver_paused: bool
    restart_requested: bool
    memory_count: int


class MessageResponse(BaseModel):
    """A simple message acknowledgement."""

    message: str
