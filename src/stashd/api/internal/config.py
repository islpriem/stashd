"""Serving the cluster config the controller authored."""

from fastapi import APIRouter, Request

from stashd.config.cluster import ConfigDocument
from stashd.domain.errors import NotFound
from stashd.schemas.internal import ClusterConfigDocument

router = APIRouter()


@router.get("/cluster-config")
async def cluster_config(request: Request) -> ClusterConfigDocument:
    document: ConfigDocument | None = request.app.state.document
    if document is None:
        raise NotFound("this daemon does not author the cluster config")
    return ClusterConfigDocument(
        revision=document.revision, content_hash=document.content_hash, text=document.text
    )
