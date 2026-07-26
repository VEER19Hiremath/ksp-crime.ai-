"""AppSail entrypoint.

Catalyst AppSail assigns the HTTP port via the X_ZOHO_CATALYST_LISTEN_PORT env
var and expects the app to start listening on it within ~10 seconds. We run
uvicorn programmatically so the port is read at runtime.
"""
import os
import sys
import traceback


def main() -> None:
    try:
        import uvicorn

        port = int(os.getenv("X_ZOHO_CATALYST_LISTEN_PORT", "9000"))
        print(f"[crime-ai] starting on 0.0.0.0:{port}", flush=True)
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=port,
            workers=1,
            proxy_headers=True,
            forwarded_allow_ips="*",
            log_level="info",
        )
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    main()
