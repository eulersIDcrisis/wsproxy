"""main.py.

Main Routes for the WSproxy server.
"""
"""server.py.

Run the main server that clients connect to.
"""
import sys
import uuid
import json
import socket
import weakref
import logging
import argparse
import itertools
import traceback
from tornado import (
    websocket, web, ioloop, iostream, httpserver, netutil, tcpserver
)
from wsproxy import util
from wsproxy.context import WsContext
from wsproxy.connection import WsServerHandler
from wsproxy.parser.json import once
from wsproxy.parser.proxy import RawProxyParser
from wsproxy.routes import (
    socks5, info, tunnel, proxy as proxy_routes
)


logger = util.get_child_logger('server')


class ServerContext(WsContext):

    def __init__(self, debug=0):
        routes = info.get_routes()
        # routes.extend(tunnel.get_routes())
        routes.extend(proxy_routes.get_routes())
        routes.extend(socks5.get_routes())

        super(ServerContext, self).__init__(routes, debug=debug)

    def find_state_for_cxn_id(self, cxn_id):
        res = self.cxn_state_mapping.get(cxn_id)
        if res:
            return res
        res = self.cxn_state_mapping.get(cxn_id)
        return res


class InfoHandler(web.RequestHandler):

    def initialize(self, context=None):
        self.context = context

    def get(self):
        clients = {
            str(cxn_id): state.get_info()
            for cxn_id, state in self.context.cxn_state_mapping.items()
        }
        self.write(dict(current_connections=clients))


class ClientInfoHandler(web.RequestHandler):

    def initialize(self, context=None):
        self.context = context
    
    async def get(self, cxn_id):
        try:
            state = self.context.find_state_for_cxn_id(cxn_id)
            if not state:
                self.set_status(404)
                self.write(dict(status=404, message="Not Found"))
                return
            # Send a request to the client to get the info for the platform.
            res = await once(state, 'info', None)
            res['ip_address'] = state.other_url
            self.write(res)
        except Exception:
            logger.exception("Error with client")
            self.set_status(500, "Internal Error")
        finally:
            await self.finish()


class CentralServer(util.IOLoopContext):

    def __init__(self, port, debug=0):
        super(CentralServer, self).__init__()
        self.port = port
        
        # Create the master context.
        self.context = ServerContext(debug=debug)

        app = web.Application([
            (r'/', WsServerHandler, dict(context=self.context)),
            (r'/client/details', InfoHandler, dict(context=self.context)),
            (r'/client/(?P<cxn_id>[^/]+)', ClientInfoHandler, dict(context=self.context))
        ])
        self.server = httpserver.HTTPServer(app)
        sockets = netutil.bind_sockets(self.port)
        self.server.add_sockets(sockets)
        self.server.start()
        
        # Add these hooks to drain cleanly.
        self.add_ioloop_drain_hook(self.server.close_all_connections)


def main():
    parser = argparse.ArgumentParser(description='Run a server endpoint.')
    parser.add_argument('--port', help="Port to bind the server to.", type=int, default=8080)
    parser.add_argument('-v', '--verbose', help="Verbose logging", action='count')

    args = parser.parse_args()

    port = args.port
    debug = args.verbose

    util.setup_default_logger(logging.DEBUG if debug else logging.INFO)
    try:
        logger.info("Running server on port: %s", port)
        server = CentralServer(port, debug=debug)
        server.run_ioloop()
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

# class ProxyWebsocketHandler(websocket.WebSocketHandler):
#     """Handle connection requests for a server.

#     This maps all of the reads and writes from the tornado Websocket to the
#     passed 'processor' aggregate class.
#     """

#     def initialize(self, context=None):
#         self._context = weakref.ref(context)
#         self._state = None
#         self.client_ip = None
#         self.client_port = None

#     @property
#     def context(self):
#         return self._context()

#     @property
#     def state(self):
#         return self._state

#     def open(self, client_cxn_id):
#         try:
#             if self.request.connection.stream is None:
#                 args = self.request.connection.context.address
#                 url = "{}:{}".format(*args)
#             else:
#                 url = "{}:{}".format(
#                     self.request.remote_ip,
#                     self.request.connection.stream.socket.getpeername()[1]
#                 )

#             # Extract the connection from the context.
#             cxn_id = uuid.UUID(client_cxn_id)
#             state = self.context.find_state_for_cxn_id(client_cxn_id)
#             self._state = weakref.ref(state)
#         except Exception:
#             self.close()

#     async def on_message(self, message):
#         asyncio.create_task(self.state.on_message(message))

#     def close(self):
#         super(ProxyWebsocketHandler, self).close()

#     def on_close(self):
#         logger.info("Closing connection.")
