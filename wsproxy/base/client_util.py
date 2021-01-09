"""client_util.py.

Client-side utilities.
"""
import socket
from tornado import websocket
from wsproxy.base.common import client_logger as logger


def get_hostname():
    try:
        hostname = socket.gethostname()
    except Exception:
        return "(not found)"
    try:
        ipaddr = socket.gethostbyaddr(hostname)
        if ipaddr:
            return u'{}-{}'.format(hostname, ipaddr)
    except Exception:
        pass
    return hostname


class WsClientConnection(object):

    def __init__(self, context, url):
        self.cxn_id = get_hostname()
        self.url = url
        self.context = context
    
    @property
    def cxn(self):
        return self._cxn

    async def open(self):
        self._cxn = await websocket.websocket_connect(
                self.url, connect_timeout=10, compression_options={},
                ping_interval=5, ping_timeout=5, max_message_size=100)

    async def run(self):
        # After connecting, listen for messages until the connection closes.
        try:
            # Once a connection comes through, register this with the state.
            self.context.add_connection(self.cxn_id, self.cxn)
            while True:
                msg = await self.cxn.read_message()
                if msg is None:
                    # Websocket closed, exit.
                    return
            self.state.process_message(msg)
        finally:
            self.context.remove_connection(self.cxn_id)
