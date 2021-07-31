"""client.py.

Module that implements the Client portion of wsproxy.

This defines the WsproxyClientService class, along with some other
features.
"""
import sys
import logging
import asyncio
import argparse
from tornado import httpclient
from wsproxy import (
    util, core
)
from wsproxy.routes import (
    socks5, info, tunnel
)
from wsproxy.authentication.manager import (
    AuthManager, BasicPasswordAuthFactory
)


logger = util.get_child_logger('client')

def create_client_service(
        url, auth_manager, debug=0,
        cert_path=None, verify_host=True):

    if cert_path:
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = verify_host
        context.load_verify_locations(cert_path)
    else:
        context = None

    request = httpclient.HTTPRequest(
        url, ssl_options=context)

    return WsproxyClientService(request, auth_manager, debug=debug)


class WsproxyClientService(util.IOLoopContext):

    def __init__(self, server_request, auth_manager, debug=0):
        super(WsproxyClientService, self).__init__()
        self.request = server_request
        self.server_url = server_request.url
        self._is_connected = asyncio.Event()

        self.ioloop.add_callback(self.run_main_loop)

        routes = info.get_routes()
        routes.extend(tunnel.get_routes())
        self.context = core.WsContext(auth_manager, routes, debug=debug)

        self._on_connect_handlers = []

    def register_on_connect_handler(self, handler):
        # 'handler' should be a coroutine that accepts "WebsocketState" as an
        # argument.
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
            cxn = core.WsClientConnection(self.context, self.request)
            try:
                state = await cxn.open()
            except Exception as exc:
                logger.warning("Could not connect %s -- Reason: %s",
                               self.server_url, str(exc))
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

    def register_socks5_server(self, socks_server_port):
        # Setup a callback to start the SOCKS5 proxy server when it connects.
        async def _socks5_callback(state):
            socks_server = socks5.ProxySocks5Server(10000, state)
            try:
                socks_server.setup()
                logger.info("Setting up SOCKSv5 proxy on port: %s",
                            socks_server.port)
                while True:
                    await asyncio.sleep(10.0)
            finally:
                socks_server.teardown()

        self.register_on_connect_handler(_socks5_callback)


def main():
    """Main entrypoint for the proxy.

    This parses the program arguments and runs the server or client, depending
    on the options passed.
    """
    parser = argparse.ArgumentParser(description='Connect to an endpoint.')
    # Logging and other 'global' options.
    parser.add_argument(
        '-v', '--verbose', action='count', help=(
            "Enable verbose (logging) output. Passing multiple times "
            "increases verbosity."))
    parser.add_argument(
        '-q', '--no-stderr-log', action='store_false', dest='stderr_log',
        help=("Disable logging to stderr. This does not affect verbosity or "
              "other settings."))
    parser.add_argument(
        '--log-file', type=str, default=None, dest='log_file',
        help="Write log to file (in addition to stderr, if configured).")
    parser.add_argument(
        'server_url', help="Proxy server URL to connect to.")
    parser.add_argument(
        '--ssl-cert', dest='cert_path', default='', type=str,
        help="Path to certificate for verification.")
    parser.add_argument(
        '--ignore-verify-host', dest='verify_host', action='store_false',
        help=("Do not verify the hostname for the certificate. Useful for "
              "running with a trusted certificate, but no DNS record."))
    parser.add_argument(
        '--username', type=str, default=None,
        help="Username to authenticate with the server.")
    parser.add_argument(
        '--password', type=str, default=None,
        help="Password to authenticate with the server.")
    parser.add_argument(
        '--socks5', type=int,
        help=("Setup socks5 proxy on the given port that tunnels through "
              "the server."))
    parser.add_argument(
        '-L', '--tunnel-port', dest='local_port', type=int, help=(
            "Permit server to make requests to this local port (i.e. -L "
            "in SSH)."))

    # Parse the arguments.
    args = parser.parse_args()

    # Setup the logger based on some of the arguments.
    debug = args.verbose or 0
    level = logging.DEBUG if debug > 0 else logging.INFO
    handlers = []
    if args.stderr_log:
        handlers.append(logging.StreamHandler(sys.stderr))
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    util.setup_default_logger(handlers, level)

    # Parse the client-side parameters.
    url = args.server_url
    cert_path = getattr(args, 'cert_path', None)
    verify_host = getattr(args, 'verify_host', True)

    if args.username and args.password:
        auth_manager = BasicPasswordAuthFactory(
            args.username, args.password
        ).create_auth_manager()
    else:
        auth_manager = AuthManager()

    service = create_client_service(
        url, auth_manager, cert_path=cert_path,
        verify_host=verify_host, debug=debug)

    # NOTE: Since this is the client connecting to the server, we can
    # permit all access once the server accepts the permission here.
    if args.socks5:
        service.register_socks5_server(args.socks5)
    try:
        service.run_ioloop()
    except Exception:
        util.main_logger.exception("Error running Client service!")


if __name__ == '__main__':
    main()
