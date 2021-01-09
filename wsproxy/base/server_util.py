"""server_util.py

Server-side Websocket connection utilities.
"""
import weakref
from tornado import websocket
from wsproxy.base.common import server_logger as logger


class WsServerConnection(websocket.WebSocketHandler):
    """Single connection to the server.
    
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
    def client_url(self):
        return '{}:{}'.format(self.client_ip, self.client_port)

    def open(self):
        if self.request.connection.stream is None:
            args = self.request.connection.context.address
            self.client_ip = args[0]
            self.client_port = args[1]
        else:
            self.client_ip = self.request.remote_ip
            self.client_port = self.request.connection.stream.socket.getpeername()[1]
        
        self.cxn_id = '{}:{}'.format(self.client_ip, self.client_port) or uuid.uuid1().hex

        logger.info("Client connection received from: %s", self.client_url)
        logger.info("Received at URL: %s", self.request.full_url())

        self._state = self.context.add_connection(self.cxn_id, self)

    def on_close(self):
        try:
            logger.info("Closing connection from: %s", self.client_url)
            context = self.context
            if context:
                context.remove_connection(self.cxn_id)
        finally:
            self._state = None

    async def on_message(self, message):
        await self.state.on_message(message)
