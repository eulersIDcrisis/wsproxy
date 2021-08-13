"""main.py.

Main entrypoint for using the wsproxy tool. There is a single
point of configuration for both client and server (and hybrid)
usage, as well as the different configuration options.
"""
import sys
import logging
import argparse
import yaml
from tornado import web, httpserver, httpclient, netutil, ioloop
from wsproxy import core, util
from wsproxy.routes import registry as route_registry
from wsproxy.authentication.manager import BasicPasswordAuthFactory
from wsproxy.service.config import get_default_config_file_contents

logger = util.get_child_logger('main')


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

    unix_socket_path = server_options.get('unix_socket')
    if unix_socket_path:
        logger.info("Running server on UNIX path: %s", unix_socket_path)    

        unix_app = web.Application(routes)
        unix_server = httpserver.HTTPServer(unix_app)
        unix_socket = netutil.bind_unix_socket(unix_socket_path)
        unix_server.add_sockets([unix_socket])
        unix_server.start()
        servers.append(unix_server)

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


def run_from_options(options):
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

    auth_options = options.get('auth', {})
    username = auth_options.get('username', 'admin')
    password = auth_options.get('password', 'password')

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
    parser = argparse.ArgumentParser(description='Run wsproxy.')
    parser.add_argument(
        '-c', '--config', type=str, help="Configuration file to run the server.")
    parser.add_argument(
        '--generate-default', action='store_true', dest='generate',
        help="Generate a default configuration file and print to stdout.")

    args = parser.parse_args()
    if args.generate:
        print(get_default_config_file_contents())
        sys.exit(0)
        return

    if not args.config:
        print("No configuration file specified. Exiting")
        sys.exit(1)
        return

    with open(args.config, 'r') as stm:
        options = yaml.safe_load(stm)

    run_from_options(options)


if __name__ == '__main__':
    main()
