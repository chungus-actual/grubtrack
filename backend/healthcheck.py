"""Docker healthcheck: the API answers on this container's own port, over HTTP or HTTPS."""

import os
import ssl
import sys
import urllib.request

scheme = "https" if os.environ.get("GT_TLS_CERT") else "http"
url = f"{scheme}://127.0.0.1:{os.environ.get('GT_PORT', '8888')}/api/build"
try:
    urllib.request.urlopen(url, timeout=4, context=ssl._create_unverified_context() if scheme == "https" else None)
except Exception as e:
    print(e)
    sys.exit(1)
