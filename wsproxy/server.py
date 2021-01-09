"""server.py.

Run the main server that clients connect to.
"""
import sys
import json
import socket
import weakref
import logging
import traceback
from tornado import (
    websocket, web, ioloop, iostream, httpserver, netutil, tcpserver
)
from wsproxy.lib.context import JsonWsContext, WsServerConnection
from wsproxy.lib import common


logger = common.get_child_logger('server')


class InfoHandler(web.RequestHandler):

    def initialize(self, context=None):
        self.context = context

    def get(self):
        self.write(dict(
            clients=[
                key.get_info() for key in self.context.ws_mapping.keys()
            ],
            servers=[
                str(value.local_servers)
                for value in self.context.ws_mapping.values()
            ]
        ))


class CentralServer(common.IOLoopService):

    def __init__(self, port, **kwargs):
        super(CentralServer, self).__init__(**kwargs)
        self.port = port
        self.context = JsonWsContext([
        ])

        app = web.Application([
            (r'/', WsServerConnection, dict(context=self.context)),
            (r'/info', InfoHandler, dict(context=self.context)),
        ])
        self.server = httpserver.HTTPServer(app)
        sockets = netutil.bind_sockets(self.port)
        self.server.add_sockets(sockets)
        self.server.start()
        
        # Add these hooks to drain cleanly.
        self.add_ioloop_drain_hook(self.server.close_all_connections)


def main(port):
    common.setup_default_logger()
    try:
        server = CentralServer(port)    
        server.run_ioloop()
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
