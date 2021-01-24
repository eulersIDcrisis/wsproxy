#!/usr/bin/python
# -*- coding: utf-8 -*-
"""client.py.

Client-side websocket connection that connects to the WS server
and tunnels the socket accordingly.
"""
import sys
import socket
import logging
import asyncio
import argparse
from tornado import websocket, iostream, httpclient, gen
from tornado.ioloop import IOLoop

from wsproxy import util
from wsproxy.util import client_logger as logger
from wsproxy.context import WsContext
from wsproxy.connection import WsClientConnection
from wsproxy.routes import info, tunnel, socks5, proxy as proxy_routes
from wsproxy.parser.proxy import RawProxyParser


class ClientService(util.IOLoopContext):

    def __init__(self, server_url, debug=0):
        super(ClientService, self).__init__()
        self.server_url = server_url
        self._is_connected = asyncio.Event()
        self.ioloop.add_callback(self.run_main_loop)
        
        routes = info.get_routes()
        routes.extend(proxy_routes.get_routes())
        # routes.extend(tunnel.get_routes())
        self.context = WsContext(routes, debug=debug)

        self._on_connect_handlers = []

    def register_on_connect_handler(self, handler):
        # 'handler' should be a coroutine that accepts "WebsocketState" as an argument.
        self._on_connect_handlers.append(handler)

    @property
    def is_connected_event(self):
        return self._is_connected

    def get_websocket_state(self):
        cxn_id = next(iter(self.context.cxn_state_mapping))
        return self.context.cxn_state_mapping.get(cxn_id)
    
    async def run_main_loop(self):
        while True:
            connected = await self._run_connection()
            # If the connection never occurred, stall for 5 seconds.
            if not connected:
                await asyncio.sleep(5)
                continue
        logger.info("Stopping main loop.")

    async def _run_connection(self):
        try:
            cxn = WsClientConnection(self.context, self.server_url)
            try:
                state = await cxn.open()
            except Exception as exc:
                logger.warning("Could not connect %s -- Reason: %s", self.server_url, str(exc))
                return
            self._is_connected.set()

            await asyncio.gather(*[
                handler(state) for handler in self._on_connect_handlers
            ])

            await cxn.run()
        except Exception:
            logger.exception("Error in run_main_loop()")
        finally:
            self._is_connected.clear()


def main():
    parser = argparse.ArgumentParser(description='Connect to an endpoint.')
    parser.add_argument('server_url', help="The server to connect to.")
    parser.add_argument('--socks5', help="Proxy through the server endpoint.", action='store_true')
    parser.add_argument("--echo", help="Run the echo routes.", action='store_true')
    parser.add_argument("--register", help="Port to run local server on.", type=int)
    parser.add_argument('-v', '--verbose', help="Verbose logging", action='count')

    args = parser.parse_args()

    debug = args.verbose or 0

    util.setup_default_logger(logging.DEBUG if debug > 0 else logging.INFO)
    try:
        service = ClientService(args.server_url, debug=debug)

        if args.socks5:
            # Setup a callback to start the SOCKS5 proxy server when it connects.
            async def _socks5_callback(state):
                socks_server = socks5.ProxySocks5Server(10000, state)
                try:
                    socks_server.setup()
                    logger.info("Setting up SOCKSv5 proxy on port: %s", socks_server.port)
                    while True:
                        await asyncio.sleep(10.0)
                finally:
                    socks_server.teardown()

            service.register_on_connect_handler(_socks5_callback)

        service.run_ioloop()
    except Exception:
        logger.exception("Error in program!")


if __name__ == "__main__":
    main()
