"""FastAPI application factory for Lamaru Local AI Gateway."""

import json
import os
import time
from typing import AsyncIterator, List, Optional
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from lamaru.domain.errors import ErrorCategory, LamaruError
from lamaru.domain.messages import AIRequest, Message, MessageRole
from lamaru.domain.models import TaskRequirement
from lamaru.domain.tools import ToolDefinition
from lamaru.gateway.models import (
    AgentRunRequest,
    AgentRunResponse,
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatUsage,
    ModelItem,
    ModelListResponse,
)
from lamaru.models.registry import ModelRegistry
from lamaru.providers.registry import ProviderRegistry
from lamaru.router.fallback import FallbackEngine
from lamaru.router.router import ModelRouter
from lamaru.tools.security import SecretSanitizer


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Ensures every HTTP request has an x-request-id header for tracing."""

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["x-request-id"] = req_id
        return response


security_bearer = HTTPBearer(auto_error=False)


async def verify_gateway_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> None:
    """Validate gateway auth token if LAMARU_GATEWAY_TOKEN is configured in environment.

    The /health endpoint is exempt to allow local health probing.
    """
    if request.url.path == "/health":
        return

    expected_token = os.environ.get("LAMARU_GATEWAY_TOKEN")
    if not expected_token:
        return  # Local request allowed if no token is configured (default dev mode)

    if not credentials or credentials.credentials != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Gateway Token")


def create_app(
    fallback_engine: Optional[FallbackEngine] = None,
    model_registry: Optional[ModelRegistry] = None,
    provider_registry: Optional[ProviderRegistry] = None,
    allowed_hosts: Optional[List[str]] = None,
) -> FastAPI:
    """Create and configure the FastAPI application for Lamaru AI Gateway."""
    # Register gateway token dynamically in SecretSanitizer if configured
    if expected_token := os.environ.get("LAMARU_GATEWAY_TOKEN"):
        SecretSanitizer.register_secret(expected_token)

    app = FastAPI(
        title="Lamaru AI Gateway",
        description="Local AI Gateway and Agent Runtime",
        version="0.1.0",
        dependencies=[Depends(verify_gateway_token)],
    )

    hosts = (
        allowed_hosts
        if allowed_hosts is not None
        else ["127.0.0.1", "localhost", "testserver", "test"]
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=hosts,
    )
    app.add_middleware(RequestIDMiddleware)

    # Dependencies
    m_reg = model_registry or ModelRegistry()
    p_reg = provider_registry or ProviderRegistry()
    if fallback_engine is not None:
        fb_engine = fallback_engine
    else:
        router = ModelRouter(model_registry=m_reg, provider_registry=p_reg)
        fb_engine = FallbackEngine(router=router, provider_registry=p_reg)

    # ==========================================
    # Global Exception Handlers
    # ==========================================

    @app.exception_handler(LamaruError)
    async def lamaru_error_handler(request: Request, exc: LamaruError):
        req_id = getattr(request.state, "request_id", "unknown")
        status_code = 400
        if exc.category in (ErrorCategory.RATE_LIMIT, ErrorCategory.QUOTA_EXCEEDED):
            status_code = 429
        elif exc.category == ErrorCategory.AUTH_ERROR:
            status_code = 401
        elif exc.category == ErrorCategory.MODEL_NOT_FOUND:
            status_code = 404
        elif exc.category == ErrorCategory.TEMPORARY_SERVER_ERROR:
            status_code = 503

        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": exc.category.value,
                    "message": exc.message,
                    "request_id": req_id,
                }
            },
        )

    # ==========================================
    # Health & Models Endpoints
    # ==========================================

    @app.get("/health")
    async def health_check():
        return {
            "status": "ok",
            "service": "lamaru-ai-gateway",
            "version": "0.1.0",
        }

    @app.get("/v1/models", response_model=ModelListResponse)
    async def list_models():
        models = m_reg.list_models(enabled_only=True)
        items = [
            ModelItem(id=m.model_id, owned_by=m.provider)
            for m in models
        ]
        return ModelListResponse(data=items)

    # ==========================================
    # OpenAI-Compatible Chat Completions
    # ==========================================

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: ChatCompletionRequest,
        request: Request,
    ):
        req_id = getattr(request.state, "request_id", f"req-{uuid.uuid4().hex[:12]}")

        # Map to internal messages
        messages = []
        for m in payload.messages:
            role_enum = MessageRole.USER
            if m.role == "system":
                role_enum = MessageRole.SYSTEM
            elif m.role == "assistant":
                role_enum = MessageRole.ASSISTANT
            elif m.role == "tool":
                role_enum = MessageRole.TOOL

            messages.append(Message(role=role_enum, content=m.content, name=m.name))

        # Map tools
        tools = []
        if payload.tools:
            for t in payload.tools:
                fn = t.get("function", t)
                tools.append(
                    ToolDefinition(
                        name=fn.get("name", "unnamed_tool"),
                        description=fn.get("description", ""),
                        input_schema=fn.get("parameters", {}),
                    )
                )

        ai_request = AIRequest(
            messages=messages,
            model=payload.model,
            temperature=payload.temperature or 0.7,
            max_tokens=payload.max_tokens,
            stream=bool(payload.stream),
            tools=tools,
        )

        task_req = TaskRequirement(
            task_type="coding" if tools else "general_chat",
            requires_tools=bool(tools),
        )

        # Handle Non-streaming
        if not payload.stream:
            fallback_res = await fb_engine.execute_with_fallback(
                request=ai_request, task=task_req
            )
            res = fallback_res.response

            return ChatCompletionResponse(
                id=f"chatcmpl-{req_id}",
                created=int(time.time()),
                model=res.model,
                choices=[
                    ChatChoice(
                        index=0,
                        message={
                            "role": "assistant",
                            "content": res.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": json.dumps(tc.arguments),
                                    },
                                }
                                for tc in res.tool_calls
                            ],
                        },
                        finish_reason=res.finish_reason.value,
                    )
                ],
                usage=ChatUsage(
                    prompt_tokens=res.usage.input_tokens,
                    completion_tokens=res.usage.output_tokens,
                    total_tokens=res.usage.total_tokens,
                ),
            )

        # Handle Streaming
        async def event_generator() -> AsyncIterator[str]:
            fallback_res = await fb_engine.execute_with_fallback(
                request=ai_request, task=task_req
            )
            provider = p_reg.get(fallback_res.selected_candidate.provider)
            if not provider:
                yield f"data: {json.dumps({'error': 'Provider not available'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for chunk in provider.stream(ai_request):
                chunk_payload = {
                    "id": f"chatcmpl-{req_id}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": fallback_res.selected_candidate.model_id,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": chunk.content_delta or "",
                            },
                            "finish_reason": (
                                chunk.finish_reason.value
                                if chunk.finish_reason
                                else None
                            ),
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk_payload)}\n\n"

            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # ==========================================
    # Agent Endpoints
    # ==========================================

    @app.post("/v1/agent/run", response_model=AgentRunResponse)
    async def agent_run(payload: AgentRunRequest):
        session_id = f"session-{uuid.uuid4().hex[:8]}"

        task_req = TaskRequirement(task_type="coding", requires_tools=True)
        ai_req = AIRequest(
            messages=[Message(role=MessageRole.USER, content=payload.task)],
            model="default",
        )

        res = await fb_engine.execute_with_fallback(ai_req, task=task_req)

        return AgentRunResponse(
            session_id=session_id,
            status="COMPLETED",
            task=payload.task,
            result=res.response.content,
            model_used=res.selected_candidate.model_id,
            iterations=1,
        )

    @app.post("/v1/agent/stream")
    async def agent_stream(payload: AgentRunRequest):
        async def sse_agent_events() -> AsyncIterator[str]:
            session_id = f"session-{uuid.uuid4().hex[:8]}"

            events = [
                {"event": "started", "session_id": session_id, "task": payload.task},
                {"event": "planning", "plan": ["Inspect requirements", "Execute request"]},
                {"event": "model_selected", "model": "gemini-2.5-flash"},
                {"event": "text_delta", "delta": "Agent execution starting..."},
                {"event": "completed", "status": "COMPLETED"},
            ]

            for ev in events:
                yield f"event: {ev['event']}\ndata: {json.dumps(ev)}\n\n"

        return StreamingResponse(sse_agent_events(), media_type="text/event-stream")

    return app
