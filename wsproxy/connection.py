"""connection.py.

Basic connection types for wsproxy.
"""
import uuid
import weakref
import asyncio
from tornado import websocket
from wsproxy.util import main_logger as logger


def generate_connection_id():
    return uuid.uuid1().hex[4:8]


class WsClientConnection(object):
    """Handle an outgoing connection to some server."""

    def __init__(self, context, url):
        self.cxn_id = generate_connection_id()
        self.url = url
        self.context = context
        self._is_connected = asyncio.Event()

    @property
    def is_connected(self):
        return self._is_connected

    @property
    def cxn(self):
        return self._cxn

    @property
    def state(self):
        return self._state

    async def open(self):
        self._cxn = await websocket.websocket_connect(
            self.url, connect_timeout=10, compression_options={},
            ping_interval=5, ping_timeout=15)
        state = await self.context.add_outgoing_connection(
            self.cxn_id, self, other_url=self.url)
        asyncio.create_task(self._run())
        self._is_connected.set()
        self._state = state
        return state

    async def _run(self):
        # After connecting, listen for messages until the connection closes.
        try:
            while True:
                msg = await self.cxn.read_message()
                if msg is None:
                    return
                asyncio.create_task(self.state.on_message(msg))
        finally:
            await self.context.remove_outgoing_connection(self.cxn_id)
            self._is_connected.clear()

    async def write_message(self, msg, binary=False):
        await self._cxn.write_message(msg, binary=binary)


class WsServerHandler(websocket.WebSocketHandler):
    """Handle connection requests for a server.
    
    This maps all of the reads and writes from the tornado Websocket to the
    passed 'processor' aggregate class.
    """

    def initialize(self, context=None):
        self._context = weakref.ref(context)
        self._state = None
        self.client_ip = None
        self.client_port = None
        self.cxn_id = None

    @property
    def context(self):
        return self._context()

    @property
    def state(self):
        return self._state

    @property
    def url(self):
        return u'{}:{}'.format(self.client_ip, self.client_port)

    async def open(self):        
        self.cxn_id = generate_connection_id()
        if self.request.connection.stream is None:
            args = self.request.connection.context.address
            url = "{}:{}".format(*args)
        else:
            url = "{}:{}".format(
                self.request.remote_ip,
                self.request.connection.stream.socket.getpeername()[1]
            )
        self._state = await self.context.add_incoming_connection(self.cxn_id, self, other_url=url)
        logger.info("Client CXN (ID: %s) received from: %s", self.cxn_id, self._state.other_url)
        logger.info("Received at URL: %s", self.request.full_url())
        

    def on_close(self):
        try:
            logger.info("Closing client CXN with ID: %s", self.cxn_id)
            context = self.context
            if context:
                asyncio.create_task(context.remove_incoming_connection(self.cxn_id))
                # await context.remove_incoming_connection(self.cxn_id)
        finally:
            self._state = None

    async def on_message(self, message):
        asyncio.create_task(self.state.on_message(message))
