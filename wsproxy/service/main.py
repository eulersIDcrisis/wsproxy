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
import click
from tornado import web, httpserver, httpclient, netutil, ioloop
from wsproxy import core, util, auth
from wsproxy.parser.json import once, setup_subscription
from wsproxy.routes import registry as route_registry
from wsproxy.authentication.manager import BasicPasswordAuthFactory
from wsproxy.service.config import get_default_config_file_contents


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


WSPROXY_VERSION = '0.1.0'
logger = util.get_child_logger('main')


def create_root_cli():
    @click.group(context_settings=dict(
        help_option_names=['-h', '--help']
    ))
    @click.version_option(WSPROXY_VERSION)
    def _cli():
        pass

    return _cli


main_cli = create_root_cli()


@main_cli.command('run', help=(
    "Run wsproxy server/client, using the given configuration file. "))
@click.argument('config_file', type=click.Path(
    exists=True, file_okay=True, readable=True))
@click.option('-v', '--verbose', count=True, help=(
    "Enable verbose output. This option stacks for increasing verbosity."))
def run_cli(config_file, verbose):
    # Parse
    level = logging.DEBUG if verbose > 0 else logging.INFO
    handlers = []
    handlers.append(logging.StreamHandler(sys.stderr))
    # if args.log_file:
    #     handlers.append(logging.FileHandler(args.log_file))
    util.setup_default_logger(handlers, level)

    with open(config_file, 'r') as stm:
        options = yaml.safe_load(stm)

    run_with_options(options)


class AdminContext(object):

    def __init__(self, url='/tmp/wsproxy.sock', config_options=None):
        self.url = u'{}'.format(url)
        self.config_options = config_options if config_options else dict()

    def get_connection(self):
        auth_manager = _parse_auth_manager(self.config_options)
        if not auth_manager:
            raise Exception('Error: no authentication credentials parsed!')
        url = self.url
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

        cert_path = self.config_options.get('cert_path')
        if cert_path:
            import ssl

            verify_host = self.config_options.get('verify_host', True)

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


@main_cli.group('admin', help="Run administrative commands for wsproxy.")
@click.option('-c', '--config', type=click.Path(
    exists=True, file_okay=True, readable=True))
@click.option('--url', type=str, help="URL to connect to.")
@click.option('-u', '--user', type=str, help=(
    "Username for wsproxy. This overrides the same setting in the config "
    "file."
))
@click.option('-w', '--password', type=str, help=(
    "Password for wsproxy. This overrides the same setting in the config "
    "file."
))
@click.pass_context
def admin_cli(ctx, url, config, user, password):
    if config:
        with open(config, 'r') as stm:
            options = yaml.safe_load(stm)
        # Store the configuration with the context, after checking the other
        # options. This should permit various different forms of auth later.
    else:
        options = dict()

    if 'auth' not in options:
        options['auth'] = dict()
    if user:
        options['auth']['username'] = user
    if password:
        options['auth']['password'] = password

    if not url:
        url = 'unix://unix_localhost/tmp/wsproxy.sock'

    ctx.obj = AdminContext(url, options)


async def run_list_command(cxn):
    await cxn.open()
    res = await once(cxn.state, 'connection_info', dict())
    stm = click.get_text_stream('stdout')
    json.dump(res, stm, indent=2)
    # Add a newline at the end for good measure.
    click.echo('')


pass_admin_context = click.pass_obj


@admin_cli.command('list', help="List connections for the given wsproxy.")
@pass_admin_context
def list_command(admin_context):
    cxn = admin_context.get_connection()
    try:
        loop = asyncio.new_event_loop()
        task = loop.create_task(run_list_command(cxn))
        loop.run_until_complete(task)
    except Exception:
        logger.exception("Error running command!")
    finally:
        loop.close()


async def run_socks_proxy(cxn, port):
    args = dict(port=port)
    await cxn.open()
    async with setup_subscription(
        cxn.state, 'socks5_proxy', args
    ) as sub:
        async for msg in sub.result_generator():
            click.echo(msg)


@admin_cli.command('socks5', help=(
    "Setup a Socks5 proxy to tunnel through the given wsproxy server. "
))
@click.argument('socks_port', type=click.IntRange(0, 65535))
@pass_admin_context
def socks5_command(admin_context, socks_port):
    cxn = admin_context.get_connection()
    try:
        loop = asyncio.new_event_loop()
        task = loop.create_task(run_socks_proxy(cxn, socks_port))
        loop.run_until_complete(task)
    except Exception:
        logger.exception("Error running command!")
    finally:
        loop.close()


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

    socks5_parser = admin_cmds.add_parser(
        'socks5', help='Setup a SOCKS5 proxy for the given client.')
    socks5_parser.add_argument('cxn_id', help=(
        'Connection ID to tunnel the SOCKS proxy through..'))
    socks5_parser.add_argument(
        'port', type=int, help='The port to run the SOCKS server on.')

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
    main_cli()
    # main()
