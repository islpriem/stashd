"""What a daemon reports about the work it is doing."""

from fastapi import APIRouter

from stashd.api.deps import Cluster, Session, Store, Ticking
from stashd.schemas.internal import EventAccepted, TransferEvent
from stashd.services.events import Event, apply_event

router = APIRouter()


@router.post("/events")
async def transfer_event(
    session: Session, clock: Ticking, cluster: Cluster, store: Store, body: TransferEvent
) -> EventAccepted:
    applied, transfer = await apply_event(
        session,
        clock,
        Event(
            transfer_id=body.transfer_id,
            sequence=body.sequence,
            kind=body.kind,
            daemon_id=body.daemon_id,
            at=body.at,
            bytes_done=body.bytes_done,
            files_done=body.files_done,
            failure=body.failure,
            message=body.message,
        ),
        cluster,
        store,
    )
    return EventAccepted(transfer_id=transfer.id, applied=applied, state=str(transfer.state))
