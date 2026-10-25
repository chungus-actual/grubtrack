"""Start GrubTrack: uvicorn on GT_PORT (8888), over HTTPS when GT_TLS_CERT and GT_TLS_KEY are set."""

import os

import uvicorn

if __name__ == "__main__":
    cert, key = os.environ.get("GT_TLS_CERT"), os.environ.get("GT_TLS_KEY")
    uvicorn.run("main:app", host=os.environ.get("GT_HOST", "0.0.0.0"), port=int(os.environ.get("GT_PORT", "8888")),
                ssl_certfile=cert or None, ssl_keyfile=key or None, proxy_headers=True)
