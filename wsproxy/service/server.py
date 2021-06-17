"""server.py.

Module that implements the Server portion of wsproxy.

This defines the WsproxyServer class, along with some other features.
"""
import sys
import uuid
import logging
import argparse
import traceback
from tornado import web, httpserver, netutil
from wsproxy import (
    util, core
)
from wsproxy.routes import (
    socks5, info, tunnel
)


# Bring the logger into scope here.
logger = util.get_child_logger('server')


def get_context(debug=0):
    routes = info.get_routes()
    routes.extend(tunnel.get_routes())
    routes.extend(socks5.get_routes())

    return core.WsContext(routes, debug=debug)


class LoginHandler(web.RequestHandler):

    def post(self):
        try:
            req = json.loads(self.request.body)

            user = req['user']
            password = req['password']

            res = json.dumps(req)
            self.set_secure_cookie(res)
            self.write(dict(status=200, message="Logged in successfully."))
        except Exception:
            self.set_status(403)
            self.write(dict(status=403, message="Invalid login."))


class WsproxyBaseHandler(web.RequestHandler):

    def initialize(self, *args, **kwargs):
        self.context = None

    async def prepare(self):
        try:
            self.context = self.application.settings['context']
            res = self.get_secure_cookie('wsproxy')
            if res:
                self.current_user = res
        except Exception:
            pass


class InfoHandler(WsproxyBaseHandler):

    def get(self):
        clients = {
            str(cxn_id): state.get_info()
            for cxn_id, state in self.context.cxn_state_mapping.items()
        }
        self.write(dict(current_connections=clients))


class ClientInfoHandler(WsproxyBaseHandler):

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


class ClientPortHandler(WsproxyBaseHandler):

    async def post(self, cxn_id):
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


class WsproxyService(util.IOLoopContext):

    def __init__(self, debug=0):
        super().__init__()
        
        # Create the master context.
        self.context = get_context(debug=debug)

        routes = [
            (r'/api/login', LoginHandler),
            (r'/api/ws', core.WsServerHandler, dict(context=self.context)),
            (r'/api/client/details', InfoHandler),
            (r'/api/client/(?P<cxn_id>[^/]+)', ClientInfoHandler),
            (r'/api/client/(?P<cxn_id>[^/]+)/tunnel', ClientPortHandler)
        ]

        # Set the cookie secret.
        cookie_secret = '{}{}'.format(uuid.uuid4().hex, uuid.uuid4().hex)
        self.app = web.Application(routes, cookie_secret=cookie_secret, context=self.context)
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


def main():
    """Main entrypoint for the proxy.

    This parses the program arguments and runs the server or client, depending
    on the options passed.
    """
    parser = argparse.ArgumentParser(description='Run a wsproxy server.')

    # Logging and other 'global' options.
    parser.add_argument('-v', '--verbose', action='count', help=(
        "Enable verbose (logging) output. Passing multiple times increases verbosity."))
    parser.add_argument('-q', '--no-stderr-log', action='store_false', dest='stderr_log',
        help=("Disable logging to stderr. This does not affect verbosity or other "
              "settings."))
    parser.add_argument('--log-file', type=str, default=None, dest='log_file',
        help="Write log to file (in addition to stderr, if configured).")

    # Ports for the server.
    parser.add_argument('-p', '--port', type=int, default=8080)
    parser.add_argument('--unix-socket', type=str, nargs='*', dest='unix_sockets')

    # Configure the certificate parameters, if passed.
    parser.add_argument(
        '--ssl-cert', dest='cert_path', default='', nargs=2, type=str,
        metavar=('CERT_FILE', 'KEY_FILE'),
        help="Path to cert file and key file to use when running with SSL.")

    # Parse the args and run the server.
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

    # Setup the rest of the server.
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
        util.main_logger.info("Running server on port: %s", port)
        server = WsproxyService(debug=debug)

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


if __name__ == '__main__':
    main()
