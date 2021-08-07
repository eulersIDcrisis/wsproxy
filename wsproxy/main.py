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


logger = util.get_child_logger('main')


def _parse_server_options(context, server_options):

    # TODO -- permit configuring which routes are allowed.
    port = server_options.get('port', 8080)
    unix_socket = server_options.get('unix_socket')
    logger.info("Running server on port: %d", port)
    if unix_socket:
        logger.info("Running server on UNIX path: %s", unix_socket)

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
    app = web.Application(routes, ssl_context=ssl_ctx)
    server = httpserver.HTTPServer(app)
    sockets = netutil.bind_sockets(port)
    if unix_socket:
        sockets.append(netutil.bind_unix_socket(unix_socket))
    server.add_sockets(sockets)
    server.start()
    return server


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
    server_options = options.get('server', {})
    if server_options.get('enabled', False):
        server = _parse_server_options(context, server_options)

    clients = []
    for client_options in options.get('clients', []):
        print("CLIENT OPTS: ", client_options)
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

    with open(args.config, 'r') as stm:
        options = yaml.safe_load(stm)

    run_from_options(options)


def get_default_config_file_contents():
    """Write out the default file contents for a reference file."""
    return """
# wsproxy configuration file.
---
logging:
  # Set the output for the logger. If 'stderr', then write to stderr.
  file: stderr

# Set as a (nonnegative) integer. Higher numbers imply more verbose
# output. Currently, this goes up to 3.
debug: 0

# Auth Configuration
#
# Configuration options to authenticate this server and client.
username: admin
password: password

# Server Configuration
#
# Configuration options when running as a server. The server is only
# enabled if the 'enabled' key is true.
server:
  # If false, the server is disabled entirely.
  enabled: true

  # Port to run the server on.
  port: 8080

  # Optionally listen on a UNIX socket as well. The `$(pid)` will be
  # substituted with the process ID of the server when spawned.
  unix_socket: "/tmp/wsproxy.$(pid)"

  # Configure the SSL options for the server.
  ssl:
    # If true, SSL is enabled, otherwise no.
    enabled: true

    # Certificate options.
    cert_path: "/path/to/cert.pem"
    key_path: "/path/to/cert.key"

# Client Configuration
#
# Configuration options when running as a client (when this wsproxy
# instance will attempt to connect to another wsproxy server). Unlike
# the server options, multiple clients can be configured.
clients:
 -  client:
    enabled: true
    url: "wss://wsproxyserver.com"
    ssl:
      enabled: true
      cert_path: "/path/to/verify/cert"
      # Set to true to include verifying the host as a part of the
      # validation.
      verify_host: false
"""

if __name__ == '__main__':
    main()
