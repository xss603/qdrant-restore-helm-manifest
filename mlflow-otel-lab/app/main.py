import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# 1. Service Resource Configuration
service_name = os.getenv("OTEL_SERVICE_NAME", "sample-fastapi-service")
resource = Resource.create(
    attributes={
        SERVICE_NAME: service_name,
        "service.version": "1.0.0",
        "deployment.environment": "lab",
    }
)

# 2. TracerProvider & OTLP gRPC Exporter setup
provider = TracerProvider(resource=resource)
otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
insecure_env = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"

exporter = OTLPSpanExporter(
    endpoint=otlp_endpoint,
    insecure=insecure_env,
)

span_processor = BatchSpanProcessor(
    exporter,
    schedule_delay_millis=500,
    max_export_batch_size=10,
)
provider.add_span_processor(span_processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("sample-fastapi-tracer", "1.0.0")


# 3. Background Traffic Generator
async def background_traffic_generator():
    """Generates initial background traces on startup after a warm-up delay."""
    await asyncio.sleep(4)
    print("[background_traffic_generator] Starting automatic trace generation...")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        for i in range(3):
            try:
                res = await client.post(
                    "/process",
                    json={"traffic_type": "startup_auto", "iteration": i + 1},
                )
                print(
                    f"[background_traffic_generator] Sent startup trace {i + 1}/3: status {res.status_code}"
                )
            except Exception as exc:
                print(f"[background_traffic_generator] Traffic generator error: {exc}")
            await asyncio.sleep(1.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    gen_task = asyncio.create_task(background_traffic_generator())
    try:
        yield
    finally:
        gen_task.cancel()
        provider.force_flush()
        provider.shutdown()


# 4. FastAPI Application Setup
app = FastAPI(title="Sample OTel Instrumented Service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": service_name}


@app.post("/process")
@app.get("/process")
async def process(request: Request, payload: Optional[Dict[str, Any]] = None):
    with tracer.start_as_current_span("process_workflow") as root_span:
        root_span.set_attribute("workflow.name", "gen_ai_orchestration")
        client_ip = request.client.host if request.client else "unknown"
        root_span.set_attribute("http.client_ip", client_ip)
        root_span.set_attribute("http.method", request.method)

        # Nested span 1: validate_payload
        with tracer.start_as_current_span("validate_payload") as val_span:
            val_span.set_attribute("validation.status", "passed")
            val_span.set_attribute("validation.schema_version", "v1.2")
            val_span.set_attribute("validation.rules_checked", 5)
            await asyncio.sleep(0.04)

        # Nested span 2: db_lookup_mock
        with tracer.start_as_current_span("db_lookup_mock") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.name", "rag_knowledge_base")
            db_span.set_attribute("db.operation", "SELECT")
            db_span.set_attribute(
                "db.statement",
                "SELECT context, source FROM knowledge_chunks WHERE sim_score > 0.85 LIMIT 3;",
            )
            db_span.set_attribute("db.rows_returned", 3)
            await asyncio.sleep(0.06)

        # Nested span 3: compute_inference_mock with GenAI semantic conventions
        with tracer.start_as_current_span("compute_inference_mock") as llm_span:
            llm_span.set_attribute("gen_ai.operation.name", "chat")
            llm_span.set_attribute("gen_ai.provider.name", "anthropic")
            llm_span.set_attribute("gen_ai.request.model", "claude-3-5-sonnet")
            llm_span.set_attribute("gen_ai.request.temperature", 0.7)
            llm_span.set_attribute("gen_ai.request.max_tokens", 1024)
            llm_span.set_attribute("gen_ai.response.finish_reasons", ["stop"])
            llm_span.set_attribute("gen_ai.usage.prompt_tokens", 128)
            llm_span.set_attribute("gen_ai.usage.completion_tokens", 256)
            llm_span.set_attribute("gen_ai.usage.total_tokens", 384)
            llm_span.set_attribute("custom.cache_hit", False)
            llm_span.set_attribute("custom.latency_target_met", True)
            await asyncio.sleep(0.10)

        # Force flush to deliver traces promptly to collector
        provider.force_flush()

        return {
            "status": "success",
            "service": service_name,
            "workflow": "gen_ai_orchestration",
            "model": "claude-3-5-sonnet",
            "tokens": 384,
            "spans_generated": [
                "process_workflow",
                "validate_payload",
                "db_lookup_mock",
                "compute_inference_mock",
            ],
        }


# Auto-instrument FastAPI routes
FastAPIInstrumentor.instrument_app(app)
