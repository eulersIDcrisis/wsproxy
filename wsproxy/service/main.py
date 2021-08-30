"""main.py.

Main entrypoint for using the wsproxy tool. There is a single
point of configuration for both client and server (and hybrid)
usage, as well as the different configuration options.
"""
import os
import sys
import socket
import logging
import argparse
import yaml
from tornado import web, httpserver, httpclient, netutil, ioloop
from wsproxy import core, util
from wsproxy.routes import registry as route_registry
from wsproxy.authentication.manager import BasicPasswordAuthFactory
from wsproxy.service.config import get_default_config_file_contents


WSPROXY_VERSION = '0.1.0'
logger = util.get_child_logger('main')


class UnixResolver(netutil.Resolver):
    """Resolver that also resolves for UNIX sockets."""

    def initialize(self, resolver, unix_sockets, *args, **kwargs):
        self.resolver = resolver
        self.unix_sockets = unix_sockets

    def close(self):
        self.resolver.close()

    async def resolve(self, host, port, *args, **kwargs):
        if host in self.unix_sockets:
            return [(socket.AF_UNIX, self.unix_sockets[host])]
        result = await self.resolver.resolve(host, port, *args, **kwargs)
        return result


def _create_admin_socket(context, path=None):
    path = path or '/tmp/wsproxy.sock'

    routes = [
        (r'/ws', core.WsServerHandler, dict(context=context)),
    ]

    logger.info("Running admin socket on UNIX path: %s", path)
    unix_app = web.Application(routes)
    unix_server = httpserver.HTTPServer(unix_app)
    unix_socket = netutil.bind_unix_socket(path)
    unix_server.add_sockets([unix_socket])
    unix_server.start()
    return unix_server


def _parse_server_options(context, server_options):
    servers = []
    # TODO -- permit configuring which routes are allowed.
    port = server_options.get('port', 8080)
    logger.info("Running server on port: %d", port)

    ssl_options = server_options.get('ssl', {})
    if ssl_options.get('enabled', False):
        import ssl

        cert_path = ssl_options['cert_path']
        key_path = ssl_options['key_path']

        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cert_path, key_path)
        logger.info("Enabling SSL with cert: %s", cert_path)
    else:
        ssl_ctx = None
        logger.warning("SSL DISABLED")

    routes = [
        (r'/ws', core.WsServerHandler, dict(context=context)),
    ]
    port_app = web.Application(routes)
    port_server = httpserver.HTTPServer(port_app, ssl_options=ssl_ctx)
    sockets = netutil.bind_sockets(port)
    port_server.add_sockets(sockets)
    port_server.start()
    servers.append(port_server)

    return servers


def _parse_client_options(context, client_options):
    url = client_options['url']
    logger.info('Client will connect to URL: %s', url)
    custom_ssl = client_options.get('ssl', {})
    if custom_ssl.get('enabled', False):
        cert_path = custom_ssl.get('cert_path')
        # Assume true by default.
        verify_host = custom_ssl.get('verify_host', True)
    else:
        cert_path = None

    if cert_path:
        import ssl

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        ssl_context.check_hostname = verify_host
        ssl_context.load_verify_locations(cert_path)
        logger.info('Using cert chain: %s (verify host: %s)',
                    cert_path, 'True' if verify_host else 'False')
    else:
        ssl_context = None

    request = httpclient.HTTPRequest(url, ssl_options=ssl_context)
    return core.WsClientConnection(context, request)


def run_with_options(options):
    # Parse the different fields.
    log_file = options.get('file')
    debug = options.get('debug', 1)

    # Initialize the logger.
    level = logging.DEBUG if debug > 0 else logging.INFO
    handlers = []
    handlers.append(logging.StreamHandler(sys.stderr))
    # if args.log_file:
    #     handlers.append(logging.FileHandler(args.log_file))
    util.setup_default_logger(handlers, level)

    username = options.get('username', 'admin')
    password = options.get('password', 'password')

    # auth_options = options.get('auth', {})
    # username = auth_options.get('username', 'admin')
    # password = auth_options.get('password', 'password')

    # Parse the auth manager.
    auth_manager = BasicPasswordAuthFactory(
        username, password
    ).create_auth_manager()

    # Setup the routes.
    route_mapping = route_registry.get_route_mapping()
    context = core.WsContext(auth_manager, route_mapping, debug=debug)

    # Parse server options.
    servers = []
    server_options = options.get('server', {})
    if server_options.get('enabled', False):
        servers = _parse_server_options(context, server_options)

    clients = []
    for client_options in options.get('clients', []):
        client = _parse_client_options(context, client_options)
        clients.append(client)

    # Exit early if there is nothing to run.
    if not servers and not clients:
        logger.error("No servers or clients configured to run. Exiting...")
        return

    # Otherwise, also setup the admin UNIX server socket.
    admin_server = _create_admin_socket(context)
    servers.append(admin_server)

    # Run with all of the options now passed.
    try:
        loop = ioloop.IOLoop.current()
        # Queue up all of the clients to run.
        for client in clients:
            loop.add_callback(client.run)

        # Start the loop.
        loop.start()
        loop.close()
    except Exception:
        logger.exception('Error running event loop!')


def run_admin_mode(args):
    pass


def main():
    default_config = os.path.join(os.getcwd(), 'config.yml')

    parent_parser = argparse.ArgumentParser(add_help=False)
    global_options = parent_parser.add_argument_group(
        title="Global Options")
    # Set the default configuration file path.
    global_options.add_argument(
        '-c', '--config', type=str, default=default_config,
        help='Config file to run the server. (default: %(default)s)')
    global_options.add_argument(
        '-v', '--verbose', action='count', default=0, dest='debug',
        help='Enable more verbose output.')
    global_options.add_argument(
        '--version', action='version', version=WSPROXY_VERSION)

    parser = argparse.ArgumentParser(
        parents=[parent_parser],
        description='Run and manage a websocket proxy.')

    subparsers = parser.add_subparsers(
        title='Available Commands', help='Additional Help', dest='cmd_name',
        description='Commands to run and manage existing websocket proxies.')

    run_parser = subparsers.add_parser(
        'run', help='Run wsproxy server/client.', parents=[parent_parser])
    run_parser.set_defaults(func=run_with_options)
    run_parser.add_argument(
        '--generate-default', action='store_true', dest='generate',
        help='Generate a default configuration file and print to stdout.')

    admin_parser = subparsers.add_parser(
        'admin', help='Manage running wsproxy server.',
        parents=[parent_parser])
    admin_parser.set_defaults(func=run_admin_mode)
    admin_parser.add_argument(
        '-u', '--user', type=str, default=None,
        help='Username for login. (Default parsed from config file.)')
    admin_parser.add_argument(
        '-p', '--password', type=str, default=None,
        help='Password for login. (Default parsed from config file.)')
    admin_parser.add_argument(
        '--url', type=str, default=None, help='Connect using the given URL.')
    admin_parser.add_argument(
        '--no-ssl', action='store_true',
        help=('Permit connecting without SSL. Careful, as this can send auth '
              'without encryption.'))
    admin_cmds = admin_parser.add_subparsers(
        title='Administration Commands', dest='cmd')
    admin_cmds.add_parser('list', help='List current connections.')
    admin_cmds.add_parser(
        'socks5', help='Setup a SOCKS5 proxy for the given client.')

    args = parser.parse_args()
    if not getattr(args, 'func', None):
        parser.print_help()
        sys.exit(1)
        return

    try:
        with open(args.config, 'r') as stm:
            options = yaml.safe_load(stm)
    except Exception:
        options = {}

    # Add the command line overrides.
    debug = getattr(args, 'debug', None)
    if debug is not None:
        options['debug'] = debug

    # After overriding with the CLI options.
    args.func(options)


if __name__ == '__main__':
    main()
