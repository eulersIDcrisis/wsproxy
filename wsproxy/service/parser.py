"""parser.py.

Module to parse configuration options into the wsproxy service.
"""
import os
import re
from tornado import web, ioloop, httpserver, httpclient, netutil
from wsproxy import auth, core, util
from wsproxy.routes import registry as route_registry
from wsproxy.service import misc

logger = util.get_child_logger('parser')


SOCKS5_INIT_REGEX = re.compile(r'socks5\:(?P<port>[0-9]+)')
TUNNEL_INIT_REGEX = re.compile(r'tunnel\:(?P<port>[0-9]+)')


def parse_auth_manager(options):
    """Parse out the auth manager for a particular set of options."""
    manager = None
    auth_options = options.get('auth', dict())
    if not auth_options:
        return None

    auth_type = auth_options.get('type', 'basic').lower()
    allowed_hosts = auth_options.get('allowed_hosts', [])
    kwargs = dict(allowed_hosts=allowed_hosts)
    if auth_type == 'basic':
        subject = auth_options.get('username')
        password = auth_options.get('password')
        manager = auth.BasicPasswordAuthManager(subject, password, **kwargs)

    # Parse init handlers.
    for handler_option in auth_options.get('init_handlers', []):
        m = SOCKS5_INIT_REGEX.match(handler_option)
        if m:
            port = int(m.groups()[0])
            async def _socks_callback(state):
                await misc.run_socks_proxy(port, state)
            manager.add_init_handler(_socks_callback)
            continue

        m = TUNNEL_INIT_REGEX.match(handler_option)
        if m:
            port = int(m.groups()[0])
            async def _tunnel_callback(state):
                await misc.run_tunneled_server(port, state)
            manager.add_init_handler(_tunnel_callback)

    return manager


def parse_server_options(context, server_options):
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
        manager = parse_auth_manager(client)
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


def parse_client_options(context, client_options):
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


def _create_admin_server(context, path=None):
    if path is None:
        path = '/tmp/wsproxy.sock'
        if os.path.exists(path):
            # Update the path to attempt to be unique.
            path = '{}.{}'.format(path, os.getpid())

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


def run_with_options(options):
    debug = options.get('debug', 0)
    # These are the main username/password fields for this user, not for
    # other users that could authenticate with this instance.
    auth_manager = parse_auth_manager(options)
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
        servers = parse_server_options(context, server_options)

    clients = []
    for client_options in options.get('clients', []):
        client = parse_client_options(context, client_options)
        clients.append(client)

    # Exit early if there is nothing to run.
    if not servers and not clients:
        logger.error("No servers or clients configured to run. Exiting...")
        return

    # Otherwise, also setup the admin UNIX server socket.
    admin_server = _create_admin_server(context)
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
