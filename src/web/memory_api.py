#!/usr/bin/python3

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.core.config import load_config, update_config
from src.core.lifecycle import is_deriver_paused, pause_deriver, resume_deriver
from src.memory.store import KINDS, PEER_CARD_FILES, PEERS
from src.web.deps import get_store
from src.web.schemas import (
    Conclusion,
    ConclusionUpdate,
    DeriverState,
    FreezeUpdate,
    ImportPayload,
    ManualConclusion,
    MessageResponse,
    PeerCard,
    PeerCardUpdate,
    PeerSummary,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/memory", tags=["memory"])

# config.memory keys that freeze each peer card
FREEZE_FLAGS: dict[str, str] = {"user": "freeze_user_card", "agent": "freeze_agent_card"}


def _peer_max_chars() -> int:
    """
    This function returns the configured peer-card character budget.
    """
    config: dict[str, Any] = load_config()
    return int((config.get("memory") or {}).get("peer_card_max_chars", 1500))


def _is_frozen(peer: str) -> bool:
    """
    This function returns the freeze flag for a peer from config.
    """
    config: dict[str, Any] = load_config()
    flag: str | None = FREEZE_FLAGS.get(peer)
    return bool(flag and (config.get("memory") or {}).get(flag, False))


@router.get("/peers", response_model=list[PeerSummary])
def list_peers() -> list[PeerSummary]:
    """
    This endpoint returns each peer with its conclusion count and card length.
    """
    store = get_store()
    summaries: list[PeerSummary] = []
    for peer in sorted(PEERS):
        conclusions: list[dict[str, Any]] = store.list_conclusions(peer=peer, limit=100000)
        card: str = store.read_peer_card(peer=peer)
        summaries.append(PeerSummary(
            peer=peer,
            count=len(conclusions),
            card_chars=len(card),
            frozen=_is_frozen(peer=peer),
        ))
    return summaries


@router.get("/conclusions", response_model=list[Conclusion])
def list_conclusions(
    peer: str | None = Query(default=None),
    kinds: list[str] | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Conclusion]:
    """
    This endpoint lists conclusions, optionally filtered by peer/kinds and ranked by
    semantic relevance when a search query is supplied.
    """
    store = get_store()
    if q:
        matches: list[dict[str, Any]] = store.search(query=q, peer=peer, kinds=kinds, n_results=limit)
        # search() returns lightweight rows; resolve full records for editing
        results: list[Conclusion] = []
        for match in matches:
            results.append(Conclusion(
                id=match.get("id", ""),
                content=match.get("content", ""),
                peer=match.get("peer", ""),
                kind=match.get("kind", ""),
            ))
        return results
    records: list[dict[str, Any]] = store.list_conclusions(peer=peer, kinds=kinds, limit=limit, offset=offset)
    return [Conclusion(**record) for record in records]


@router.post("/conclusions", response_model=Conclusion, status_code=status.HTTP_201_CREATED)
def add_conclusion(body: ManualConclusion) -> Conclusion:
    """
    This endpoint adds an operator-authored conclusion (bypassing deduplication).
    """
    if body.peer not in PEERS or body.kind not in KINDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid peer or kind.")
    store = get_store()
    record_id: str | None = store.add_manual_conclusion(
        peer=body.peer, kind=body.kind, content=body.content, premises=body.premises,
    )
    if record_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not add conclusion.")
    record: dict[str, Any] | None = store.get_conclusion(record_id=record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Conclusion not found after add.")
    return Conclusion(**record)


@router.patch("/conclusions/{record_id}", response_model=Conclusion)
def update_conclusion(record_id: str, body: ConclusionUpdate) -> Conclusion:
    """
    This endpoint updates a conclusion's content (re-embedding), kind, and/or premises.
    """
    store = get_store()
    ok: bool = store.update_conclusion(
        record_id=record_id, content=body.content, kind=body.kind, premises=body.premises,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conclusion not found.")
    record: dict[str, Any] | None = store.get_conclusion(record_id=record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conclusion not found.")
    return Conclusion(**record)


@router.delete("/conclusions/{record_id}", response_model=MessageResponse)
def delete_conclusion(record_id: str) -> MessageResponse:
    """
    This endpoint deletes a single conclusion by id.
    """
    store = get_store()
    if not store.delete_conclusion(record_id=record_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conclusion not found.")
    return MessageResponse(message="Conclusion deleted.")


@router.delete("/peers/{peer}/conclusions", response_model=MessageResponse)
def wipe_peer(peer: str) -> MessageResponse:
    """
    This endpoint deletes every conclusion stored for a peer.
    """
    if peer not in PEERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown peer.")
    store = get_store()
    removed: int = store.delete_all_for_peer(peer=peer)
    return MessageResponse(message=f"Wiped {removed} conclusion(s) for '{peer}'.")


@router.get("/peers/{peer}/card", response_model=PeerCard)
def get_card(peer: str) -> PeerCard:
    """
    This endpoint returns a peer card and its freeze state.
    """
    if peer not in PEER_CARD_FILES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown peer.")
    store = get_store()
    return PeerCard(
        peer=peer,
        text=store.read_peer_card(peer=peer),
        max_chars=_peer_max_chars(),
        frozen=_is_frozen(peer=peer),
    )


@router.put("/peers/{peer}/card", response_model=MessageResponse)
def put_card(peer: str, body: PeerCardUpdate) -> MessageResponse:
    """
    This endpoint overwrites a peer card. The write is cross-process locked so it
    will not collide with a concurrent re-curation by the deriver.
    """
    if peer not in PEER_CARD_FILES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown peer.")
    store = get_store()
    store.write_peer_card(peer=peer, text=body.text, max_chars=_peer_max_chars())
    return MessageResponse(message=f"Peer card for '{peer}' saved.")


@router.put("/peers/{peer}/freeze", response_model=MessageResponse)
def set_freeze(peer: str, body: FreezeUpdate) -> MessageResponse:
    """
    This endpoint toggles whether the deriver may re-curate a peer card.
    """
    flag: str | None = FREEZE_FLAGS.get(peer)
    if not flag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown peer.")

    def mutate(config: dict[str, Any]) -> None:
        config.setdefault("memory", {})[flag] = body.frozen

    update_config(mutate=mutate)
    state: str = "frozen" if body.frozen else "unfrozen"
    return MessageResponse(message=f"Peer card for '{peer}' {state}.")


@router.get("/export", response_model=list[Conclusion])
def export_conclusions(peer: str | None = Query(default=None)) -> list[Conclusion]:
    """
    This endpoint exports conclusions for backup, optionally limited to one peer.
    """
    store = get_store()
    records: list[dict[str, Any]] = store.export(peer=peer)
    return [Conclusion(**record) for record in records]


@router.post("/import", response_model=MessageResponse)
def import_conclusions(body: ImportPayload) -> MessageResponse:
    """
    This endpoint imports previously exported conclusions.
    """
    store = get_store()
    count: int = store.import_records(records=body.records)
    return MessageResponse(message=f"Imported {count} conclusion(s).")


@router.get("/deriver", response_model=DeriverState)
def get_deriver_state() -> DeriverState:
    """
    This endpoint returns whether background reasoning is currently paused.
    """
    return DeriverState(paused=is_deriver_paused())


@router.put("/deriver", response_model=DeriverState)
def set_deriver_state(body: DeriverState) -> DeriverState:
    """
    This endpoint pauses or resumes background reasoning.
    """
    if body.paused:
        pause_deriver()
    else:
        resume_deriver()
    return DeriverState(paused=is_deriver_paused())
