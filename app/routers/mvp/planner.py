import json

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select, text, update

from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.mvp_planner import Conversation, ConversationTurn, Trip
from app.routers.mvp.homestays import DB, Traveler
from app.schemas.mvp import BookingDetailResponse
from app.schemas.mvp_planner import (
    Confirm, Itinerary, Message, Proposal, SavedTrip, SaveTrip, Selection, Thread, Title,
    Turn, UpdateTrip,
)
from app.services import mvp_planner as planner, mvp_planner_tools as booking
from app.services.mvp_listing_verification import now

router = APIRouter(prefix="/ai/planner", tags=["MVP Smart Travel Planner"])
limiter = SlidingWindowRateLimiter(max_requests=6, window_seconds=60)


def _schema(db):
    if db.get_bind().dialect.name == "mssql" and db.scalar(text(
        "SELECT CASE WHEN OBJECT_ID(N'dbo.ai_conversations', N'U') IS NOT NULL "
        "AND OBJECT_ID(N'dbo.ai_conversation_turns', N'U') IS NOT NULL "
        "AND OBJECT_ID(N'dbo.ai_saved_trips', N'U') IS NOT NULL THEN 1 ELSE 0 END"
    )) != 1:
        raise HTTPException(503, "Planner persistence requires MVP schema 1.0.6")


def _mine(model, traveler_id):
    return select(model).where(model.traveler_id == traveler_id, model.deleted_at.is_(None))


def _owned(db, model, key, traveler_id):
    _schema(db)
    row = db.scalar(_mine(model, traveler_id).where(model.id == key))
    if row is None:
        raise HTTPException(404, "Planner record not found")
    return row


def _thread(row):
    return Thread.model_validate(row, from_attributes=True)


def _turn(row):
    return Turn(id=row.id, user_message=row.user_message, created_at=row.created_at,
                **json.loads(row.result_json))


def _trip(row):
    return SavedTrip(**_thread(row).model_dump(),
                     itinerary=Itinerary.model_validate_json(row.itinerary_json))


@router.post("/conversations", response_model=Thread, status_code=201)
def create_conversation(payload: Title, db: DB, traveler: Traveler):
    _schema(db)
    stamp = now()
    row = Conversation(traveler_id=traveler.id, title=payload.title,
                       created_at=stamp, updated_at=stamp)
    db.add(row)
    db.commit()
    return _thread(row)


@router.get("/conversations", response_model=list[Thread])
def conversations(db: DB, traveler: Traveler, limit: int = Query(20, ge=1, le=50),
                  offset: int = Query(0, ge=0)):
    _schema(db)
    query = _mine(Conversation, traveler.id).order_by(
        Conversation.updated_at.desc(), Conversation.id.desc(),
    )
    return [_thread(row) for row in db.scalars(query.offset(offset).limit(limit))]


@router.get("/conversations/{conversation_id}", response_model=Thread)
def conversation(conversation_id: int, db: DB, traveler: Traveler):
    return _thread(_owned(db, Conversation, conversation_id, traveler.id))


def _change_conversation(db, row, **fields):
    result = db.execute(update(Conversation).where(
        Conversation.id == row.id, Conversation.revision == row.revision,
        Conversation.deleted_at.is_(None),
    ).values(**fields, revision=Conversation.revision + 1, updated_at=now())
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(409, "Conversation changed. Refresh and retry.")


@router.patch("/conversations/{conversation_id}", response_model=Thread)
def rename_conversation(conversation_id: int, payload: Title, db: DB, traveler: Traveler):
    row = _owned(db, Conversation, conversation_id, traveler.id)
    _change_conversation(db, row, title=payload.title)
    db.commit()
    db.refresh(row)
    return _thread(row)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, db: DB, traveler: Traveler):
    row = _owned(db, Conversation, conversation_id, traveler.id)
    _change_conversation(db, row, deleted_at=now())
    db.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/messages", response_model=list[Turn])
def messages(conversation_id: int, db: DB, traveler: Traveler,
             limit: int = Query(20, ge=1, le=50), before_id: int | None = Query(None, gt=0)):
    _owned(db, Conversation, conversation_id, traveler.id)
    query = select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id)
    if before_id is not None:
        query = query.where(ConversationTurn.id < before_id)
    return [_turn(row) for row in db.scalars(query.order_by(ConversationTurn.id.desc()).limit(limit))]


@router.post("/conversations/{conversation_id}/messages", response_model=Turn, status_code=201)
def send_message(conversation_id: int, payload: Message, db: DB, traveler: Traveler):
    traveler_id = traveler.id
    row = _owned(db, Conversation, conversation_id, traveler_id)
    revision = row.revision
    limiter.check_identifier(f"planner-user:{traveler_id}")
    history = db.scalars(select(ConversationTurn).where(
        ConversationTurn.conversation_id == conversation_id,
    ).order_by(ConversationTurn.id.desc()).limit(5)).all()
    reply, itinerary, listings, quotes, bookings = planner.run_turn(
        db, traveler_id, planner.history_prompt(reversed(history)), payload.text,
    )
    row = _owned(db, Conversation, conversation_id, traveler_id)
    if row.revision != revision:
        raise HTTPException(409, "Conversation changed. Refresh and retry.")
    proposal = (booking.propose(traveler, quotes[reply.booking_quote_ref].quote)
                if reply.booking_quote_ref else None)
    result = {
        "assistant_message": (
            "Review the quote and explicitly confirm if you want to request this booking."
            if proposal else "Current booking and payment status is shown below."
            if bookings else reply.message
        ),
        "itinerary": itinerary.model_dump(mode="json") if itinerary else None,
        "proposal": proposal.model_dump(mode="json") if proposal else None,
        "listings": [item.model_dump(mode="json") for item in listings.values()],
        "quotes": [item.model_dump(mode="json") for item in quotes.values()],
        "bookings": [item.model_dump(mode="json") for item in bookings.values()],
    }
    _change_conversation(db, row)
    turn = ConversationTurn(conversation_id=conversation_id, user_message=payload.text,
                            result_json=json.dumps(result), created_at=now())
    db.add(turn)
    db.commit()
    return _turn(turn)


@router.post("/booking-quotes", response_model=Proposal)
def quote(payload: Selection, db: DB, traveler: Traveler):
    return booking.propose(traveler, booking.quote(db, payload))


@router.post("/bookings/confirm", response_model=BookingDetailResponse)
def confirm(payload: Confirm, db: DB, traveler: Traveler):
    return booking.confirm(db, traveler, payload)


def _owned_itinerary(db, turn_id, traveler_id):
    row = db.scalar(select(ConversationTurn).join(
        Conversation, Conversation.id == ConversationTurn.conversation_id,
    ).where(ConversationTurn.id == turn_id, Conversation.traveler_id == traveler_id,
            Conversation.deleted_at.is_(None)))
    if row is None:
        raise HTTPException(404, "Planner turn not found")
    itinerary = _turn(row).itinerary
    if itinerary is None:
        raise HTTPException(422, "Selected turn does not contain an itinerary")
    return itinerary.model_dump_json()


@router.post("/trips", response_model=SavedTrip, status_code=201)
def save_trip(payload: SaveTrip, db: DB, traveler: Traveler):
    _schema(db)
    stamp = now()
    row = Trip(traveler_id=traveler.id, title=payload.title, created_at=stamp,
               updated_at=stamp, itinerary_json=_owned_itinerary(db, payload.turn_id, traveler.id))
    db.add(row)
    db.commit()
    return _trip(row)


@router.get("/trips", response_model=list[SavedTrip])
def trips(db: DB, traveler: Traveler, limit: int = Query(20, ge=1, le=50),
          offset: int = Query(0, ge=0)):
    _schema(db)
    query = _mine(Trip, traveler.id).order_by(Trip.updated_at.desc(), Trip.id.desc())
    return [_trip(row) for row in db.scalars(query.offset(offset).limit(limit))]


@router.get("/trips/{trip_id}", response_model=SavedTrip)
def trip(trip_id: int, db: DB, traveler: Traveler):
    return _trip(_owned(db, Trip, trip_id, traveler.id))


@router.patch("/trips/{trip_id}", response_model=SavedTrip)
def update_trip(trip_id: int, payload: UpdateTrip, db: DB, traveler: Traveler):
    row = _owned(db, Trip, trip_id, traveler.id)
    row.title, row.updated_at = payload.title, now()
    if payload.turn_id is not None:
        row.itinerary_json = _owned_itinerary(db, payload.turn_id, traveler.id)
    db.commit()
    return _trip(row)


@router.delete("/trips/{trip_id}", status_code=204)
def delete_trip(trip_id: int, db: DB, traveler: Traveler):
    row = _owned(db, Trip, trip_id, traveler.id)
    row.deleted_at = row.updated_at = now()
    db.commit()
    return Response(status_code=204)
