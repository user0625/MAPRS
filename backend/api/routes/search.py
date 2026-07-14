from __future__ import annotations

from fastapi import APIRouter

from backend.api import task_store as store_module
from backend.api.schemas import DocumentSearchRequest, DocumentSearchResponse
from backend.api.task_state import completed_task, load_task_state, validate_scope
from backend.core.config import get_settings
from backend.document_search import DocumentSearchService

router = APIRouter(tags=["document-search"])


@router.post(
    "/api/tasks/{task_id}/search",
    response_model=DocumentSearchResponse,
)
async def search_document(
    task_id: str, body: DocumentSearchRequest
) -> DocumentSearchResponse:
    task = completed_task(
        store_module.task_store,
        task_id,
        conflict_detail="Document search requires a completed task.",
    )
    state = load_task_state(
        task,
        unavailable_status=404,
        unavailable_detail="Paper state is unavailable.",
        invalid_detail="Paper state is invalid.",
    )
    validate_scope(task, state, body.section, body.page_start, body.page_end)
    result = DocumentSearchService(get_settings()).search(
        task_id,
        task.state_json_path,
        state,
        body.query,
        mode=body.mode,
        section=body.section,
        page_start=body.page_start,
        page_end=body.page_end,
        top_k=body.top_k,
    )
    return DocumentSearchResponse.model_validate(
        {
            "task_id": task_id,
            "query": body.query,
            "mode_used": result.mode_used,
            "hits": result.hits,
            "diagnostics": {
                "actual_mode": result.mode_used,
                "candidate_count": result.candidate_count,
                "elapsed_ms": round(result.elapsed_ms, 3),
                "index_source": result.index_source,
                "fallback_reason": result.fallback_reason,
            },
        }
    )
