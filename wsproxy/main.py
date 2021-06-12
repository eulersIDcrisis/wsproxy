"""main.py.

Entrypoints for the server and client.
"""
import sys
import uuid
import json
import socket
import weakref
import logging
import asyncio
import argparse
import itertools
import traceback
from tornado import (
    websocket, web, ioloop, iostream, httpserver, netutil, tcpserver
)
from wsproxy import util
from wsproxy.core import (
    WsContext, WsServerHandler, WsClientConnection
)
from wsproxy.parser.json import once
from wsproxy.parser.proxy import RawProxyParser
from wsproxy.routes import (
    socks5, info, tunnel
)


logger = util.get_child_logger('server')


def get_context(debug=0):
    routes = info.get_routes()
    routes.extend(tunnel.get_routes())
    routes.extend(socks5.get_routes())

    return WsContext(routes, debug=debug)


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
        self.context = get_context(debug=debug)

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


def server_main():
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


class ClientService(util.IOLoopContext):

    def __init__(self, server_url, debug=0):
        super(ClientService, self).__init__()
        self.server_url = server_url
        self._is_connected = asyncio.Event()
        self.ioloop.add_callback(self.run_main_loop)
        
        routes = info.get_routes()
        routes.extend(tunnel.get_routes())
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
                import traceback
                traceback.print_exc()
                logger.warning("Could not connect %s -- Reason: %s", self.server_url, str(exc))
                return
            self._is_connected.set()

            await asyncio.gather(*[
                handler(state) for handler in self._on_connect_handlers
            ])
            await cxn._run()
        except Exception:
            logger.exception("Error in run_main_loop()")
        finally:
            self._is_connected.clear()


def run_server(args):
    debug = args.verbose or 0
    port = args.port
    try:
        logger.info("Running server on port: %s", port)
        server = CentralServer(port, debug=debug)
        server.run_ioloop()
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


def run_client(args):
    debug = args.verbose or 0
    url = args.server_url
    try:
        service = ClientService(url, debug=debug)

        socks5_port = args.socks5

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


def main():
    """Main entrypoint for the proxy.

    This parses the program arguments and runs the server or client, depending
    on the options passed.
    """
    parser = argparse.ArgumentParser(description='Connect to an endpoint.')
    parser.add_argument('-v', '--verbose', action='count',
        help="Enable verbose output. Passing multiple times increases verbosity.")
    parsers = parser.add_subparsers(title='Modes and Commands', description=(
        'Different modes to operate this proxy in.'), dest='command')

    server_parser = parsers.add_parser('server', help="Run the proxy server.")
    server_parser.add_argument('-p', '--port', type=int, default=8080)
    # Handy trick to call 'run_server' with the args after parsing them.
    server_parser.set_defaults(func=run_server)

    client_parser = parsers.add_parser('client', help="Run the proxy as a client.")
    client_parser.add_argument('server_url', help="Proxy server URL to connect to.")
    client_parser.add_argument('--socks5', type=int,
        help="Setup socks5 proxy on the given port that tunnels through the server.")
    client_parser.set_defaults(func=run_client)

    # Parse the arguments.
    args = parser.parse_args()

    # Setup the logger based on some of the arguments.
    debug = args.verbose or 0
    util.setup_default_logger(logging.DEBUG if debug > 0 else logging.INFO)

    # Run the appropriate function, if set.
    if not hasattr(args, 'func'):
        # Print the help and exit.
        parser.print_help()
        return

    # Call the appropriate function.
    return args.func(args)


if __name__ == '__main__':
    main()
