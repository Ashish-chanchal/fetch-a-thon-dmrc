"""
Single-command Bureau runner for local end-to-end testing of the Multi-Agent Metro Ticket Booking System:
- `metro_bap_coordinator` (port 8000 / Bureau shared port)
- `metro_bpp_catalog_agent`
- `metro_bpp_ticketing_agent`
"""

import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import threading
import httpx

import common.config as cfg  # sets SSL_CERT_FILE and GRPC_DEFAULT_SSL_ROOTS_FILE_PATH before grpc init
from uagents import Bureau
from uagents.asgi import ASGIServer

# Ensure any HTTP request to "/" or "" is mapped to "/submit" so Agentverse root probes and dispatches succeed
_orig_asgi_call = ASGIServer.__call__


async def _aliased_asgi_call(self, scope, receive, send):
    if scope.get("type") == "http":
        path = scope.get("path", "")
        # Alias root path "/" to "/submit" so Agentverse root probes and dispatches succeed
        if path in ("/", ""):
            scope["path"] = "/submit"
            return await _orig_asgi_call(self, scope, receive, send)

        # Serve QR image inline: /qr/{order_id} or /qr/{order_id}.png
        if path.startswith("/qr/"):
            order_id = path.split("/qr/")[-1].replace(".png", "").strip()
            png_path = cfg.TICKETS_DIR / f"{order_id}.png"
            if png_path.exists():
                data = png_path.read_bytes()
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"image/png"],
                        [b"content-disposition", f'inline; filename="{order_id}_qr.png"'.encode()],
                        [b"content-length", str(len(data)).encode()],
                        [b"access-control-allow-origin", b"*"],
                        [b"cache-control", b"public, max-age=86400"],
                    ],
                })
                await send({"type": "http.response.body", "body": data})
                return
            await send({
                "type": "http.response.start",
                "status": 404,
                "headers": [[b"content-type", b"application/json"]],
            })
            await send({"type": "http.response.body", "body": b'{"error": "QR image not found"}'})
            return

        # Direct file download: /download/{order_id}
        if path.startswith("/download/"):
            order_id = path.split("/download/")[-1].replace(".png", "").strip()
            png_path = cfg.TICKETS_DIR / f"{order_id}.png"
            if png_path.exists():
                data = png_path.read_bytes()
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"image/png"],
                        [b"content-disposition", f'attachment; filename="dmrc_ticket_{order_id}.png"'.encode()],
                        [b"content-length", str(len(data)).encode()],
                        [b"access-control-allow-origin", b"*"],
                    ],
                })
                await send({"type": "http.response.body", "body": data})
                return
            await send({
                "type": "http.response.start",
                "status": 404,
                "headers": [[b"content-type", b"application/json"]],
            })
            await send({"type": "http.response.body", "body": b'{"error": "Ticket not found"}'})
            return

    return await _orig_asgi_call(self, scope, receive, send)


ASGIServer.__call__ = _aliased_asgi_call


from agents.catalog_agent import catalog_agent
from agents.coordinator_agent import coordinator
from agents.ticketing_agent import ticketing_agent


class _LocalBecknSandboxHandler(BaseHTTPRequestHandler):
    """Fallback local ONDC:TRV11 v2.1.0 Beckn Sandbox server when external port 3000 sandbox is not running."""

    def log_message(self, format: str, *args) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        ctx = body.get("context") or {}
        ack_payload = {
            "context": ctx,
            "message": {"ack": {"status": "ACK"}},
        }
        encoded = json.dumps(ack_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def ensure_local_beckn_sandbox() -> None:
    """
    If ENABLE_LOCAL_BECKN_SANDBOX is enabled (default in dev) and no external sandbox is running,
    starts an embedded Beckn Sandbox server so `/search`, `/select`, `/init`, and `/confirm` succeed out-of-the-box.
    """
    if not cfg.ENABLE_LOCAL_BECKN_SANDBOX:
        return

    if "localhost" not in cfg.ONDC_GATEWAY_URL and "127.0.0.1" not in cfg.ONDC_GATEWAY_URL:
        return
    try:
        r = httpx.post(
            f"{cfg.ONDC_GATEWAY_URL}/search",
            json={"context": {"action": "search"}, "message": {}},
            timeout=1.5,
            trust_env=False,
        )
        if r.status_code == 200 and "ack" in r.text.lower():
            return
    except Exception:
        pass

    import agents.catalog_agent as cat_mod
    import agents.ticketing_agent as tick_mod

    for port in (3001, 3002):
        try:
            srv = HTTPServer(("127.0.0.1", port), _LocalBecknSandboxHandler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            sandbox_url = f"http://127.0.0.1:{port}"
            os.environ["ONDC_GATEWAY_URL"] = sandbox_url
            os.environ["ONDC_DEFAULT_BPP_URI"] = sandbox_url
            cfg.ONDC_GATEWAY_URL = sandbox_url
            cfg.ONDC_DEFAULT_BPP_URI = sandbox_url
            cat_mod.ONDC_GATEWAY_URL = sandbox_url
            cat_mod.ONDC_DEFAULT_BPP_URI = sandbox_url
            cat_mod.ondc_client.gateway_url = sandbox_url
            cat_mod.ondc_client.default_bpp_uri = sandbox_url
            tick_mod.ONDC_DEFAULT_BPP_URI = sandbox_url
            tick_mod.ondc_client.gateway_url = sandbox_url
            tick_mod.ondc_client.default_bpp_uri = sandbox_url
            break
        except OSError:
            continue


def ensure_agentverse_mailbox():
    """Auto-provisions agent mailbox in Agentverse on startup if ENABLE_MAILBOX=True and AGENTVERSE_KEY is set."""
    if not cfg.ENABLE_MAILBOX:
        return
    key = cfg.AGENTVERSE_KEY or os.environ.get("AGENTVERSE_KEY", "").strip()
    if not key:
        return

    from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
    from uagents_core.registration import RegistrationRequest

    async def _register():
        try:
            profile = coordinator._build_registration_profile()
            reg_data = RegistrationRequest(
                address=coordinator.address,
                name=coordinator.name,
                handle=coordinator._handle,
                url=None,
                profile=profile,
                endpoints=coordinator._endpoints,
                protocols=list(coordinator.protocols.keys()),
                metadata=coordinator.metadata,
            )
            req = AgentverseConnectRequest(
                user_token=key,
                agent_type="mailbox",
            )
            resp = await register_in_agentverse(
                request=req,
                identity=coordinator._identity,
                prefix=coordinator._prefix,
                agentverse=coordinator._agentverse,
                agent_details=reg_data,
            )
            if resp.success:
                print(f"[MAILBOX] Auto-provisioned Agentverse mailbox for {coordinator.name} ({coordinator.address})")
            else:
                print(f"[MAILBOX] Agentverse mailbox notice: {resp.detail}")
        except Exception as exc:
            print(f"[MAILBOX] Note: Mailbox registration check skipped ({exc})")

    try:
        cur_loop = asyncio.get_event_loop()
        if cur_loop.is_running():
            asyncio.create_task(_register())
        else:
            cur_loop.run_until_complete(_register())
    except RuntimeError:
        asyncio.run(_register())


def create_metro_bureau(port: int = 8000, loop: asyncio.AbstractEventLoop | None = None) -> Bureau:
    """Creates a uAgents Bureau hosting all three metro booking agents for the active environment."""
    is_valid, issues = cfg.validate_environment()
    if issues:
        for issue in issues:
            print(f"[CONFIG] {issue}")
        if not is_valid and cfg.STRICT_CONFIG_VALIDATION:
            raise RuntimeError(
                f"Cannot start Bureau in {cfg.APP_ENV} mode due to critical configuration issues:\n"
                + "\n".join(issues)
            )

    ensure_local_beckn_sandbox()
    ensure_agentverse_mailbox()
    if cfg.ENABLE_MAILBOX:
        # The coordinator is a mailbox agent; the internal agents only talk inside this process.
        bureau_endpoint = f"http://localhost:{port}/submit"
    elif cfg.AGENT_PUBLIC_ENDPOINT:
        public_url = cfg.AGENT_PUBLIC_ENDPOINT
        if not public_url.endswith("/submit"):
            public_url = f"{public_url.rstrip('/')}/submit"
        bureau_endpoint = [public_url]
    else:
        bureau_endpoint = f"http://localhost:{port}/submit"

    bureau = Bureau(
        port=port,
        endpoint=bureau_endpoint,
        network=cfg.FETCHAI_NETWORK,
        loop=loop,
    )

    bureau.add(coordinator)
    bureau.add(catalog_agent)
    bureau.add(ticketing_agent)
    return bureau



if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    metro_bureau = create_metro_bureau(port=port)
    metro_bureau.run()
