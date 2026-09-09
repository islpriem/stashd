"""Reports, for admins only."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from stashd.api.deps import Caller, Cluster, Session, caller_is_admin
from stashd.schemas.reports import (
    AllocationReport,
    AllocationRow,
    Offender,
    UsageGroup,
    UsageReport,
)
from stashd.services import reports
from stashd.services.filesets import Forbidden

router = APIRouter()


def _admin_only(caller: Caller, config: Cluster) -> None:
    if not caller_is_admin(caller, config):
        raise Forbidden("only an admin may read reports")


@router.get("/reports/usage")
async def usage(
    caller: Caller,
    config: Cluster,
    session: Session,
    group_by: Annotated[str, Query()] = "user",
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> UsageReport:
    """What was moved in a window, grouped as asked."""
    _admin_only(caller, config)
    grouping = reports.group_by_from(group_by)
    groups = await reports.usage_report(
        session, cluster=config, group_by=grouping, since=since, until=until
    )
    return UsageReport(
        group_by=str(grouping),
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        groups=[UsageGroup(**asdict(group)) for group in groups],
    )


@router.get("/reports/allocation")
async def allocation(caller: Caller, config: Cluster, session: Session) -> AllocationReport:
    """Who holds what, and whose filesets are past what they reserved."""
    _admin_only(caller, config)
    rows, offenders = await reports.allocation_report(session, cluster=config)
    return AllocationReport(
        rows=[AllocationRow(**asdict(row)) for row in rows],
        offenders=[Offender(**asdict(offender)) for offender in offenders],
    )
