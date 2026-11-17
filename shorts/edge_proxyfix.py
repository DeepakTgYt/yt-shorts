"""Make edge-tts work behind an env proxy whose username contains ':' (aiohttp refuses it)."""
import os, base64
from urllib.parse import unquote
import aiohttp

def apply():
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not p or "@" not in p:
        return
    rest = p.split("://", 1)[1]
    ui, host = rest.rsplit("@", 1)
    user, pw = ui.split(":", 1)
    token = base64.b64encode(f"{unquote(user)}:{unquote(pw)}".encode()).decode()
    for k in list(os.environ):
        if k.lower() in ("https_proxy", "http_proxy", "all_proxy", "wss_proxy", "ws_proxy"):
            os.environ.pop(k)
    orig = aiohttp.ClientSession._ws_connect
    async def patched(self, url, **kw):
        kw["proxy"] = "http://" + host
        kw["proxy_headers"] = {"Proxy-Authorization": "Basic " + token}
        return await orig(self, url, **kw)
    aiohttp.ClientSession._ws_connect = patched
