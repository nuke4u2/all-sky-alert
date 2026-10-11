"""Explicit trusted-LAN policy; forwarding is configured at the server boundary."""
from ipaddress import ip_address, ip_network
import re
from urllib.parse import urlsplit

PRIVATE = tuple(ip_network(v) for v in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10"))


def local_address(value):
    try:
        address = ip_address(value.split("%")[0])
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback or any(address.version == n.version and address in n for n in PRIVATE)
    except (ValueError, AttributeError):
        return False


def validate_network(s):
    if not isinstance(s["lan_access"], bool) or not isinstance(s["port"], int) or isinstance(s["port"], bool) or not 1024 <= s["port"] <= 65535:
        raise ValueError("Set boolean lan_access and port 1024–65535")
    bind = s["bind_host"]
    if bind != "localhost":
        try:
            address = ip_address(bind)
        except (ValueError, TypeError):
            raise ValueError("bind_host must be localhost or an explicit IP") from None
        if not (address.is_unspecified or local_address(bind)) or (not s["lan_access"] and not address.is_loopback):
            raise ValueError("Non-loopback binding requires trusted LAN access; public binding is forbidden")
    hosts = s["allowed_hosts"]
    if not isinstance(hosts, list) or not hosts or len(hosts) > 30:
        raise ValueError("Set explicit allowed_hosts")
    for host in hosts:
        if not isinstance(host, str) or not host or "*" in host or len(host) > 253:
            raise ValueError("Wildcard/invalid hosts are forbidden")
        try:
            ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
                raise ValueError("Invalid allowed host") from None
    proxies = s["trusted_proxy_ips"]
    if not isinstance(proxies, list) or len(proxies) > 10:
        raise ValueError("trusted_proxy_ips must be explicit IPs")
    for value in proxies:
        try:
            ip_address(value)
        except (ValueError, TypeError):
            raise ValueError("Proxy wildcards/CIDRs/hostnames are forbidden") from None
        if not local_address(value):
            raise ValueError("Only explicit local proxy IPs are supported")


class LANMiddleware:
    def __init__(self, app, enabled):
        self.app, self.enabled = app, enabled

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.enabled and not local_address((scope.get("client") or ("",))[0]):
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"Trusted LAN clients only"})
            return
        await self.app(scope, receive, send)


class ExactHostMiddleware:
    """Bracket-aware IPv6 host validation without wildcard or redirect behavior."""
    def __init__(self,app,allowed_hosts):
        self.app,self.hosts=app,{host.lower() for host in allowed_hosts}

    async def __call__(self,scope,receive,send):
        if scope['type']=='http':
            values=[value for key,value in scope['headers'] if key.lower()==b'host']
            try:
                if len(values)!=1:raise ValueError('Expected one Host')
                raw=values[0].decode('ascii')
                if any(ord(c)<=32 for c in raw) or any(c in raw for c in '/\\@?#'):raise ValueError('Invalid Host')
                parsed=urlsplit('//'+raw)
                _=parsed.port
                host=parsed.hostname
                if not host or host.lower() not in self.hosts:raise ValueError('Host not allowed')
            except (ValueError,UnicodeError):
                await send({'type':'http.response.start','status':400,'headers':[(b'content-type',b'text/plain')]})
                await send({'type':'http.response.body','body':b'Invalid host header'})
                return
        await self.app(scope,receive,send)
