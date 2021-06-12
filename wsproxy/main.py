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
    web, websocket, ioloop, iostream, httpserver, netutil, tcpserver,
    httpclient
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


class WsproxyServer(util.IOLoopContext):

    def __init__(self, debug=0):
        super().__init__()
        
        # Create the master context.
        self.context = get_context(debug=debug)

        self.app = web.Application([
            (r'/', WsServerHandler, dict(context=self.context)),
            (r'/client/details', InfoHandler, dict(context=self.context)),
            (r'/client/(?P<cxn_id>[^/]+)', ClientInfoHandler, dict(context=self.context))
        ])
        self.servers = []

    def bind_to_ports(self, ports=None, unix_sockets=None):
        if not ports and not unix_sockets:
            return

        ports = ports or []
        unix_sockets = unix_sockets or []

        server = httpserver.HTTPServer(self.app)
        for port in ports:
            sockets = netutil.bind_sockets(port)
            server.add_sockets(sockets)

        # Also include any UNIX sockets, as needed.
        for path in unix_sockets:
            socket = netutil.bind_unix_socket(path)
            server.add_sockets([socket])

        # Start the server, so it can process once the loop starts.
        server.start()
        self.servers.append(server)
        # Add these hooks to drain cleanly.
        self.add_ioloop_drain_hook(server.close_all_connections)

    def bind_to_ssl_ports(self, ports, cert_path, key_path):
        import ssl

        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert_path, key_path)
        server = httpserver.HTTPServer(self.app, ssl_options=ssl_ctx)
        for port in ports:
            sockets = netutil.bind_sockets(port)
            server.add_sockets(sockets)
        server.start()
        self.servers.append(server)
        self.add_ioloop_drain_hook(server.close_all_connections)


class ClientService(util.IOLoopContext):

    def __init__(self, server_request, debug=0):
        super(ClientService, self).__init__()
        self.request = server_request
        self.server_url = server_request.url
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
            cxn = WsClientConnection(self.context, self.request)
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
    unix_sockets = args.unix_sockets or []
    cert_paths = getattr(args, 'cert_path', [])
    if len(cert_paths) == 2:
        cert_path = cert_paths[0]
        key_path = cert_paths[1]
    else:
        cert_path = None
        key_path = None

    try:
        logger.info("Running server on port: %s", port)
        server = WsproxyServer(debug=debug)

        if cert_path and key_path:
            server.bind_to_ssl_ports([port], cert_path, key_path)
            # Do NOT bind UNIX sockets with SSL for now.
            server.bind_to_ports([], unix_sockets=unix_sockets)
        else:
            server.bind_to_ports([port], unix_sockets=unix_sockets)
        server.run_ioloop()
        sys.exit(0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


def run_client(args):
    debug = args.verbose or 0
    url = args.server_url
    cert_path = getattr(args, 'cert_path', '')
    verify_host = getattr(args, 'verify_host', True)

    if cert_path:
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = verify_host
        context.load_verify_locations(cert_path)
    else:
        context = None

    try:
        request = httpclient.HTTPRequest(
            url, ssl_options=context)
        service = ClientService(request, debug=debug)

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
    parser.add_argument('-v', '--verbose', action='count', help=(
        "Enable verbose output. Passing multiple times increases verbosity."))
    parsers = parser.add_subparsers(title='Modes and Commands', description=(
        'Different modes to operate this proxy in.'), dest='command')

    server_parser = parsers.add_parser('server', help="Run the proxy server.")
    server_parser.add_argument('-p', '--port', type=int, default=8080)
    server_parser.add_argument('--unix-socket', type=str, nargs='*',
                               dest='unix_sockets')
    server_parser.add_argument(
        '--ssl-cert', dest='cert_path', default='', nargs=2, type=str,
        metavar=('CERT_FILE', 'KEY_FILE'),
        help="Path to cert file and key file to use when running with SSL.")

    # Handy trick to call 'run_server' with the args after parsing them.
    server_parser.set_defaults(func=run_server)

    client_parser = parsers.add_parser(
        'client', help="Run the client proxy.")
    client_parser.add_argument(
        'server_url', help="Proxy server URL to connect to.")
    client_parser.add_argument(
        '--ssl-cert', dest='cert_path', default='', type=str,
        help="Path to certificate for verification.")
    client_parser.add_argument(
        '--ignore-host-verify', dest='verify_host', action='store_false')
    client_parser.add_argument(
        '--socks5', type=int, help=("Setup socks5 proxy on the given port that "
                                    "tunnels through the server."))
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
