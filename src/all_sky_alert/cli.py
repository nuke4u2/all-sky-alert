from __future__ import annotations

import argparse
import uuid

from .config import load
from .domain import iso, utcnow
from .locking import ServiceLock
from .notifications import GmailAdapter, authorize, render_message
from .service import Service


def test_alert():
    stamp = iso(utcnow())
    return {"id": f"test-{uuid.uuid4().hex}", "stage": "TEST", "prediction": {
        "candidate": {"title": "All Sky Alert test email", "start": stamp, "peak": stamp,
                      "interest": 0, "occurrence_confidence": 1,
                      "reasons": ["Explicit test delivery; this is not a sky prediction"], "sources": []},
        "assessment": {"quality": None, "confidence": 0, "reasons": [], "conditions": {"status": "test"}},
    }}


def main():
    parser = argparse.ArgumentParser(description="All Sky Alert local service")
    parser.add_argument("--config", default="config.toml", help="TOML configuration path")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the configured local/LAN UI and prediction workers")
    serve.add_argument("--host", default=None, help="Override validated configured bind_host")
    serve.add_argument("--port", type=int, default=None)
    commands.add_parser("evaluate", help="One evaluation cycle, using enabled notification adapters")
    commands.add_parser("check", help="Offline config, database write and component initialization check; stop service first; no fetch/mail/camera open")
    commands.add_parser('config-sync',help='Validate and add missing defaults, preserving all supplied values; restart required')
    commands.add_parser("reset-orbit-source", help="Explicitly clear CelesTrak HTTP stop after investigating the error; no fetch/email")
    smoke = commands.add_parser("source-smoke", help="Explicit public-source connectivity check; separate storage, no mail/credentials")
    smoke.add_argument("--source",choices=("orbits","adsb","both"),default="both")
    commands.add_parser("gmail-authorize", help="Interactive Gmail OAuth setup; sends no mail")
    test = commands.add_parser("test-email", help="Preview a test email; requires --send for delivery")
    test.add_argument("--send", action="store_true", help="Actually send to the configured recipient")
    retry = commands.add_parser("retry-delivery", help="Requeue one failed/uncertain email delivery")
    retry.add_argument("id", type=int)
    retry.add_argument("--acknowledge-possible-duplicate", action="store_true")
    args = parser.parse_args()
    try:
        config = load(args.config)
        gmail = config.values["gmail"]
        if args.command=='config-sync':
            from .config_edit import ConfigEditor
            ConfigEditor(config.file_path).sync()
            print('Configuration synchronized; existing values preserved. Restart required. Omitted detectors retain safe disabled defaults.')
            return
        if args.command == "source-smoke":
            import json
            import httpx
            from .storage import Store
            from .sources import build_sources
            directory = config.base/"test-artifacts"/"phase3-source-smoke"
            with ServiceLock(directory), httpx.Client(timeout=8,follow_redirects=False,headers={"User-Agent":"AllSkyAlert/0.1 source smoke"}) as client:
                store = Store(directory/"smoke.sqlite3")
                names = ("orbits","adsb") if args.source == "both" else (args.source,)
                sources = build_sources(client,store,config.observer,config.values["providers"],{name:["source-smoke"] for name in names},config.base)
                stamp = utcnow()
                for source in sources.values():
                    source.refresh(stamp)
                from .source_health import views
                summary = [{key:value for key,value in row.items() if key != "details"} |
                           {"http_stopped":row["details"].get("http_stopped",False)} for row in views(store,utcnow())]
                (directory/"health.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
                print(json.dumps(summary,indent=2))
                print("Separate test-artifacts storage; no mail or credentials. Cached GP checks respect the two-hour cadence.")
            return
        if args.command == "gmail-authorize":
            with ServiceLock(config.data_dir):
                authorize(config.path(gmail["client_secret"]), config.path(gmail["token"]))
            print("OAuth token saved. No email sent.")
            return
        if args.command == "test-email":
            alert = test_alert()
            if not args.send:
                print(render_message(alert, gmail["destination"] or "configure-recipient@example.invalid").as_string())
                print("Preview only. Add --send to explicitly send this test email.")
                return
            if not gmail["enabled"] or not gmail["destination"]:
                raise ValueError("Enable Gmail and configure its destination before sending")
            import httpx
            # Serialize credential refresh with the running service.
            with ServiceLock(config.data_dir), httpx.Client(timeout=20) as client:
                result = GmailAdapter(config.path(gmail["token"]), gmail["destination"], client).send(alert)
                print(f"Test email accepted by Gmail: {result}")
            return
        service = Service(config)
        if args.command == "serve":
            import uvicorn
            from .web import create_app
            from .server import ApplicationServer
            from .network import validate_network
            settings={**config.values['service'],'bind_host':config.values['service']['bind_host'] if args.host is None else args.host,
                      'port':config.values['service']['port'] if args.port is None else args.port}
            validate_network(settings)
            proxies=settings['trusted_proxy_ips']
            server_config = uvicorn.Config(create_app(service), host=settings['bind_host'], port=settings['port'], workers=1, log_level="info",
                        proxy_headers=bool(proxies),forwarded_allow_ips=','.join(proxies), timeout_graceful_shutdown=10)
            try:
                ApplicationServer(server_config, service.camera).run()
            finally:
                service.close()
            return
        try:
            with ServiceLock(config.data_dir):
                service.recover()
                if args.command == "check":
                    import platform
                    service.store.health('startup-check', 'ok', utcnow(), 'Offline configuration and SQLite write check passed')
                    print(f"Python {platform.python_version()} / {platform.machine()}")
                    print(f"Configuration valid; SQLite writable: {service.store.path}")
                    print("Initialized detectors: " + ', '.join(service.detector_names))
                    print("Initialized domain providers: " + ', '.join(service.sources))
                    print("No provider acquisition, prediction evaluation, mail or camera open performed.")
                elif args.command == "retry-delivery":
                    if not args.acknowledge_possible_duplicate:
                        raise ValueError("Explicit --acknowledge-possible-duplicate is required")
                    if not service.store.retry_delivery(args.id, utcnow()):
                        raise ValueError("No failed or uncertain delivery with that ID")
                    print("Delivery queued; next evaluation will attempt it if still eligible.")
                elif args.command == "reset-orbit-source":
                    service.store.delete_cache("celestrak-stop/v1")
                    print("CelesTrak HTTP stop cleared. No fetch or mail sent; configured cadence/backoff still applies.")
                else:
                    service.tick()
                    print(f"Evaluation complete: {len(service.upcoming())} upcoming predictions")
                    for row in service.store.health_rows():
                        print(f"{row['name']}: {row['status']} — {row['message']}")
        finally:
            service.close()
    except Exception as exc:
        # OAuth libraries can include credential-bearing requests in exception text.
        if args.command == "gmail-authorize":
            parser.exit(1, f"OAuth setup failed ({type(exc).__name__}); check desktop client and consent settings.\n")
        if isinstance(exc, (ValueError, RuntimeError, FileNotFoundError)):
            parser.exit(1, f"{exc}\n")
        parser.exit(1, f"Operation failed ({type(exc).__name__}); inspect service health.\n")


if __name__ == "__main__":
    main()
