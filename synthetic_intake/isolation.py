"""Defense-in-depth process guards; OS network-none proof is separate."""
from collections import Counter
import http.client
import os
import socket
import subprocess
import threading
import urllib.request
from unittest.mock import patch
import requests


class OutboundBlocked(RuntimeError):
    pass


class OutboundGuard:
    _process_lock=threading.Lock()
    def __init__(self):
        self.attempts=Counter()
        self._patches=[]
        self._lock=threading.Lock()

    def _deny(self,kind):
        def blocked(*args,**kwargs):
            with self._lock:self.attempts[kind]+=1
            # Do not include destinations, headers, credentials or arguments.
            raise OutboundBlocked('Synthetic endpoint blocked '+kind)
        return blocked

    def install(self):
        if self._patches:raise RuntimeError('Isolation guard already installed')
        if not self._process_lock.acquire(blocking=False):raise RuntimeError('Only one isolated adapter per process is permitted')
        targets=[(socket.socket,'connect','socket_connect'),(socket.socket,'connect_ex','socket_connect_ex'),
            (socket.socket,'sendto','socket_sendto'),(socket,'create_connection','socket_create_connection'),
            (socket,'getaddrinfo','dns'),(requests.sessions.Session,'request','http_request'),
            (urllib.request,'urlopen','urllib_request'),(http.client.HTTPConnection,'connect','http_connect'),
            (subprocess,'Popen','subprocess'),(os,'system','shell')]
        if hasattr(socket.socket,'sendmsg'):targets.append((socket.socket,'sendmsg','socket_sendmsg'))
        for obj,attr,kind in targets:
            handle=patch.object(obj,attr,self._deny(kind));handle.start();self._patches.append(handle)
        return self

    def close(self):
        installed=bool(self._patches)
        for handle in reversed(self._patches):handle.stop()
        self._patches=[]
        if installed:self._process_lock.release()

    def evidence(self):
        return {'attempts':dict(self.attempts),'total_blocked_attempts':sum(self.attempts.values()),
                'real_provider_requests_permitted':False,
                'os_isolation_qualified':False}


def network_none_inventory(proc='/proc/net'):
    """Read-only startup check; no outbound probe and no network modification."""
    from pathlib import Path
    root=Path(proc)
    devices={line.split(':',1)[0].strip() for line in (root/'dev').read_text().splitlines() if ':' in line}
    if devices!={'lo'}:raise RuntimeError('Loopback-only namespace required')
    for line in (root/'route').read_text().splitlines()[1:]:
        fields=line.split()
        if fields and fields[0]!='lo':raise RuntimeError('External IPv4 route present')
    for line in (root/'ipv6_route').read_text().splitlines():
        fields=line.split()
        if fields and fields[-1]!='lo':raise RuntimeError('External IPv6 route present')
    return {'devices':['lo'],'external_routes':False,'method':'read-only proc namespace inventory'}
