"""main.py.

Main entrypoint for using the wsproxy tool. There is a single
point of configuration for both client and server (and hybrid)
usage, as well as the different configuration options.
"""
import os
import sys
import json
import socket
import asyncio
import logging
import argparse
from urllib.parse import urlsplit, urlunsplit
import yaml
from tornado import web, httpserver, httpclient, netutil, ioloop
from wsproxy import core, util, auth
from wsproxy.parser.json import once
from wsproxy.routes import registry as route_registry
from wsproxy.authentication.manager import BasicPasswordAuthFactory
from wsproxy.service.config import get_default_config_file_contents


WSPROXY_VERSION = '0.1.0'
logger = util.get_child_logger('main')


class UnixResolver(netutil.Resolver):
    """Resolver that also resolves for UNIX sockets."""

    def initialize(self, resolver, unix_socket, *args, **kwargs):
        self.resolver = resolver
        self.unix_socket = unix_socket

    def close(self):
        self.resolver.close()

    async def resolve(self, host, port, *args, **kwargs):
        if host == 'unix_localhost':
            return [(socket.AF_UNIX, self.unix_socket)]
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


def _parse_auth_manager(options):
    """Parse out the auth manager for a particular set of options."""
    manager = None
    auth_options = options.get('auth', dict())
    if not auth_options:
        return None

    auth_type = auth_options.get('type', 'basic').lower()

    # By default, do not permit localhost or any private subnets. This options
    # will override the other filters explicitly.
    permit_localhost = auth_options.get('permit_localhost', False)
    permit_private_subnets = auth_options.get('permit_private_subnets', False)
    # TODO -- Parse out explicit IPs and ports
    kwargs = dict(
        permit_localhost=permit_localhost,
        permit_private_subnets=permit_private_subnets)

    if auth_type == 'basic':
        subject = auth_options.get('username')
        password = auth_options.get('password')
        manager = auth.BasicPasswordAuthManager(subject, password, **kwargs)

    return manager


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

    # Parse the permitted clients for this server.
    for client in server_options.get('clients', []):
        manager = _parse_auth_manager(client)
        if not manager:
            continue
        context.auth_context.add_auth_manager(manager)

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
    debug = options.get('debug', 0)
    # These are the main username/password fields for this user, not for
    # other users that could authenticate with this instance.
    auth_manager = _parse_auth_manager(options)
    if not auth_manager:
        raise Exception("No root user configured!")
    auth_context = auth.AuthContext(auth_manager)
    auth_context.add_auth_manager(auth_manager)

    # Setup the routes.
    route_mapping = route_registry.get_route_mapping()
    context = core.WsContext(auth_context, route_mapping, debug=debug)

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


def run_admin_mode(options):
    url = options.get('url', u'unix://unix_localhost/tmp/wsproxy.sock')
    auth_manager = _parse_auth_manager(options)
    if not auth_manager:
        print('Error: no authentication credentials parsed!')
        return

    # Parse the URL and handle the case if it is 'unix://'
    scheme, netloc, path, _, _ = urlsplit(url)
    if scheme.startswith(u'unix'):
        resolver = netutil.Resolver()
        netutil.Resolver.configure(
            UnixResolver, resolver=resolver, unix_socket=path
        )
        url = urlunsplit(('ws', 'unix_localhost', '/ws', '', ''))

    routes = route_registry.get_route_mapping()
    auth_context = auth.AuthContext(auth_manager)
    auth_context.add_auth_manager(auth_manager)
    context = core.WsContext(auth_context, routes)

    cert_path = options.get('cert_path')
    if cert_path:
        import ssl

        verify_host = options.get('verify_host', True)

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        ssl_context.check_hostname = verify_host
        ssl_context.load_verify_locations(cert_path)
        logger.info('Using cert chain: %s (verify host: %s)',
                    cert_path, 'True' if verify_host else 'False')
    else:
        ssl_context = None

    request = httpclient.HTTPRequest(url, ssl_options=ssl_context)
    cxn = core.WsClientConnection(context, request)
    try:
        loop = asyncio.new_event_loop()
        task = loop.create_task(_run_admin_command(cxn, 'list'))
        loop.run_until_complete(task)
    except Exception:
        logger.exception("Error running command!")
    finally:
        loop.close()


async def _run_admin_command(cxn, cmd):
    await cxn.open()
    print("Running cmd: {}".format(cmd))
    args = tuple()
    if cmd == 'list':
        res = await once(cxn.state, 'connection_info', args)
        print(json.dumps(res, indent=2))
        return
    if cmd == 'socks5':
        async with setup_subscription(
            cxn.state, 'socks5_proxy', args
        ) as sub:
            async for msg in sub.result_generator():
                print("Received: {}".format(msg))


def main():
    default_config = '/etc/wsproxy/config.yml'

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
        '-u', '--user', type=str, default=None, dest='auth_username',
        help='Username for login. (Default parsed from config file.)')
    admin_parser.add_argument(
        '-p', '--password', type=str, default=None, dest='auth_password',
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
    option_overrides = vars(args)
    skip_option_set = set(['cmd_name', 'cmd'])

    # Merge the CLI options with the parsed config.
    auth_options = options.get('auth', dict())
    for option, value in option_overrides.items():
        # Skip these options which do not pertain to anything parsable.
        if option in skip_option_set:
            continue
        # Skip these options as well (which implies they were not set).
        if value is None:
            continue
        # Parse out the auth options separately.
        if option.startswith('auth_'):
            auth_options[option[5:]] = value
        options[option] = value

    options['auth'] = auth_options

    debug = option_overrides.get('debug')
    if debug is not None:
        options['debug'] = debug

    # Initialize the logger.
    level = logging.DEBUG if options.get(debug, 0) > 0 else logging.INFO
    handlers = []
    handlers.append(logging.StreamHandler(sys.stderr))
    # if args.log_file:
    #     handlers.append(logging.FileHandler(args.log_file))
    util.setup_default_logger(handlers, level)

    # After overriding with the CLI options.
    args.func(options)


if __name__ == '__main__':
    main()
