"""server.py.

Run the main server that clients connect to.
"""
import sys
import logging
import traceback
from tornado import (
    websocket, web, ioloop, httpserver, netutil
)
from common import IOLoopService
from logger import get_child_logger, setup_default_logger

logger = get_child_logger('server')

class WsConnection(websocket.WebSocketHandler):
    """Handler that receives incoming WS connections from a client.

    This handler is responsible for registration.
    """
    def initialize(self, state=None):
        self.state = state
        self.client_ip = None
        self.client_port = None
    
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

        logger.info("Client connection received from: %s", self.client_url)
        logger.info("Received at URL: %s", self.request.full_url())

        self.state.add_connection(self)
    
    def on_message(self, message):
        logger.info("MESSAGE: %s", message)
        self.write_message(message)
    
    def on_close(self)->None:
        self.state.remove_connection(self)
        logger.info("Closing connection from: %s", self.client_url)
    
    def get_info(self):
        return dict(
            client_url=self.client_url
        )


class TunnelState(object):

    def __init__(self):
        self._mapping = {}
    
    def add_connection(self, cxn):
        self._mapping[cxn] = True
    
    def remove_connection(self, cxn):
        del self._mapping[cxn]
    
    @property
    def mapping(self):
        """Return the mapping of connections."""
        return self._mapping


class InfoHandler(web.RequestHandler):

    def initialize(self, state=None)->None:
        self.state = state

    def get(self):
        self.write(dict(
            clients=[
                key.get_info() for key in self.state.mapping.keys()
            ]
        ))


class CentralServer(IOLoopService):

    def __init__(self, port, **kwargs):
        super(CentralServer, self).__init__(**kwargs)
        self.port = port
        self.state = TunnelState()

        app = web.Application([
            (r'/', WsConnection, dict(state=self.state)),
            (r'/info', InfoHandler, dict(state=self.state)),
        ])
        self.server = httpserver.HTTPServer(app)
        sockets = netutil.bind_sockets(self.port)
        self.server.add_sockets(sockets)
        self.server.start()
        
        # Add these hooks to drain cleanly.
        self.add_ioloop_drain_hook(self.server.close_all_connections)


def main(port):
    setup_default_logger()
    try:
        server = CentralServer(port)    
        server.run_forever()
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        port = 8080
    else:
        port = int(sys.argv[1])
    main(port)
