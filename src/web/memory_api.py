#!/usr/bin/python3

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.core.config import load_config, update_config
from src.core.lifecycle import is_deriver_paused, pause_deriver, resume_deriver
from src.memory.store import KINDS, PEER_CARD_FILES, PEERS
from src.web.deps import get_store, get_event_store
from src.web.schemas import (
    Conclusion,
    ConclusionUpdate,
    DeriverState,
    Event,
    EventCreate,
    FreezeUpdate,
    ImportPayload,
    ManualConclusion,
    MessageResponse,
    PeerCard,
    PeerCardUpdate,
    PeerSummary,
    PersonCard,
    PersonSummary,
)

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(prefix="/api/memory", tags=["memory"])

# config.memory keys that freeze each peer card
FREEZE_FLAGS: dict[str, str] = {"user": "freeze_user_card"}


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
        conclusions: list[dict[str, Any]] = store.list_conclusions(peer=peer, limit=100000, entity="")
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
        return [
            Conclusion(
                id=match.get("id", ""),
                content=match.get("content", ""),
                peer=match.get("peer", ""),
                kind=match.get("kind", ""),
            )
            for match in matches
        ]
    records: list[dict[str, Any]] = store.list_conclusions(peer=peer, kinds=kinds, limit=limit, offset=offset, entity="")
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


# ----------------------------------------------------------------- people (entities)


def _entity_max_chars() -> int:
    """
    This function returns the configured per-entity card character budget.
    """
    config: dict[str, Any] = load_config()
    return int((config.get("memory") or {}).get("entity_card_max_chars", 800))


@router.get("/people", response_model=list[PersonSummary])
def list_people() -> list[PersonSummary]:
    """
    This endpoint lists every person/project/place the agent has learned about, with
    a fact count and whether it has a curated profile card.
    """
    store = get_store()
    people: list[PersonSummary] = []
    for entity in store.list_entities(min_facts=1):
        card: str = store.read_entity_card(slug=entity["slug"])
        people.append(PersonSummary(
            slug=entity["slug"],
            name=entity.get("name", "") or entity["slug"],
            type=entity.get("type", "") or "",
            relation=entity.get("relation", "") or "",
            facts=int(entity.get("facts", 0)),
            card_chars=len(card),
        ))
    return people


@router.get("/people/{slug}", response_model=PersonCard)
def get_person(slug: str) -> PersonCard:
    """
    This endpoint returns a person's profile card and the individual facts behind it.
    """
    store = get_store()
    facts: list[dict[str, Any]] = store.list_conclusions(peer="user", limit=1000, entity=slug)
    if not facts and not store.read_entity_card(slug=slug):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown person.")
    meta: dict[str, Any] = facts[0] if facts else {}
    return PersonCard(
        slug=slug,
        name=meta.get("entity_name", "") or slug,
        type=meta.get("entity_type", "") or "",
        relation=meta.get("relation", "") or "",
        card=store.read_entity_card(slug=slug),
        facts=[Conclusion(**record) for record in facts],
    )


@router.delete("/people/{slug}", response_model=MessageResponse)
def delete_person(slug: str) -> MessageResponse:
    """
    This endpoint forgets a person entirely: every stored fact and the profile card.
    """
    store = get_store()
    removed: int = store.delete_entity(slug=slug)
    return MessageResponse(message=f"Forgot '{slug}' ({removed} fact(s) removed).")


# ----------------------------------------------------------------------- events


@router.get("/events", response_model=list[Event])
def list_events() -> list[Event]:
    """
    This endpoint lists every event the agent has learned about, soonest first.
    """
    event_store = get_event_store()
    return [Event(**event) for event in event_store.list_all()]


@router.post("/events", response_model=Event, status_code=status.HTTP_201_CREATED)
def add_event(body: EventCreate) -> Event:
    """
    This endpoint adds an operator-authored event.
    """
    event_store = get_event_store()
    created: bool = event_store.add_event(
        title=body.title,
        date_iso=body.date,
        entities=body.entities,
        location=body.location,
        time_of_day=body.time_of_day,
        recurring=body.recurring,
        source="manual",
    )
    if not created:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not add event (invalid date or duplicate).",
        )
    for event in event_store.list_all():
        if event["title"] == body.title.strip() and event["date"] == body.date.strip()[:10]:
            return Event(**event)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Event not found after add.")


@router.delete("/events/{event_id}", response_model=MessageResponse)
def delete_event(event_id: str) -> MessageResponse:
    """
    This endpoint deletes a single event by id.
    """
    event_store = get_event_store()
    event_store.delete(event_id=event_id)
    return MessageResponse(message="Event deleted.")


@router.delete("/events", response_model=MessageResponse)
def wipe_events() -> MessageResponse:
    """
    This endpoint deletes every stored event.
    """
    event_store = get_event_store()
    removed: int = event_store.wipe()
    return MessageResponse(message=f"Wiped {removed} event(s).")
