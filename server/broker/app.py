from starlette.responses import JSONResponse
from telemetry import instrument

from broker.server import server


async def health(_: object) -> JSONResponse:
    return JSONResponse({"status": "ok"})


server.custom_route("/health/live", methods=["GET"])(health)
app = server.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    max_request_body_size=1_048_576,
    host="0.0.0.0",
)
instrument(app, "firekey-broker")
