from fastapi import APIRouter, HTTPException, status

from app.db.schemas import LLMConfigurationInput, LLMConfigStatusResponse, LLMTestResponse
from app.services import llm_service


router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/config", response_model=LLMConfigStatusResponse)
def get_llm_config() -> LLMConfigStatusResponse:
    """Expose effective non-secret deployment settings to the workbench."""
    return llm_service.config_status()


@router.put("/config", response_model=LLMConfigStatusResponse)
def update_llm_config(payload: LLMConfigurationInput) -> LLMConfigStatusResponse:
    """Apply browser-supplied configuration until this backend process restarts."""
    return llm_service.update_runtime_config(payload)


@router.post("/test", response_model=LLMTestResponse)
def test_llm_config(payload: LLMConfigurationInput) -> LLMTestResponse:
    try:
        reply = llm_service.test_connection(payload)
    except llm_service.LLMConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except llm_service.LLMRequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    config = llm_service.resolve_runtime_config(payload)
    return LLMTestResponse(ready=True, model=config.model, message=f"模型已连接：{reply[:160]}")
