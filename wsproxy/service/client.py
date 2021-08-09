"""client.py.

Module that implements the Client portion of wsproxy.

This defines the WsproxyClientService class, along with some other
features.
"""
import sys
import json
import socket
import logging
import asyncio
import argparse
import yaml
from tornado import netutil, httpclient, ioloop, gen
from wsproxy import (
    util, core
)
from wsproxy.parser.json import once, post, setup_subscription
import wsproxy.routes.registry as route_registry
from wsproxy.authentication.manager import (
    AuthManager, BasicPasswordAuthFactory
)


class UnixResolver(netutil.Resolver):
    def initialize(self, resolver, unix_sockets, *args, **kwargs):
        self.resolver = resolver
        self.unix_sockets = unix_sockets

    def close(self):
        self.resolver.close()

    @gen.coroutine
    def resolve(self, host, port, *args, **kwargs):
        if host in self.unix_sockets:
            return [(socket.AF_UNIX, self.unix_sockets[host])]
        result = yield self.resolver.resolve(host, port, *args, **kwargs)
        return result


def main():
    parser = argparse.ArgumentParser(description="Tool to manage a wsproxy server.")
    parser.add_argument('config', type=str, help="Configuration file for the server.")
    parser.add_argument('-l', '--list', action='store_true', help="List connection info.")
    parser.add_argument('--socks', nargs=2, metavar=("<id>", "<port>"),
        help="Setup a SOCKS5 proxy for the given client.")

    args = parser.parse_args()

    # Parse the configuration file.
    with open(args.config, 'r') as stm:
        options = yaml.safe_load(stm)

    # Parse out the authentication options and server URL.
    auth_options = options.get('auth', {})
    user = auth_options.get('username', '')
    password = auth_options.get('password', '')

    port = options.get('server', {}).get('port', 8080)
    # use_ssl = options.get('server', {}).get('ssl', {}).get('enabled', False)

    # TODO -- Require the UNIX socket when running the server?
    #
    # url = options.get('server', {}).get('unix_socket', '')
    # resolver = netutil.Resolver()
    # netutil.Resolver.configure(
    #     UnixResolver,
    #     resolver=resolver,
    #     unix_sockets={"host_on_socket": url})

    auth_manager = BasicPasswordAuthFactory(
        user, password
    ).create_auth_manager()
    routes = route_registry.get_route_mapping()
    context = core.WsContext(auth_manager, routes)
    cxn = core.WsClientConnection(context, 'ws://localhost:{}/ws'.format(port))
    loop = ioloop.IOLoop.current()
    cmds = []
    if args.list:
        cmds.append(('list', {}))

    if args.socks:
        cmds.append(('socks5', dict(
            cxn_id=args.socks[0], port=int(args.socks[1]))))
    loop.add_callback(run_cmd_loop, cxn, cmds)    
    loop.start()


async def run_cmd_loop(cxn, cmd_tuples=None):
    await cxn.open()
    asyncio.create_task(cxn._run_read_loop())
    if not cmd_tuples:
        cmd_tuples = []

    for cmd, args in cmd_tuples:
        await run_command(cxn, cmd, args)

    ioloop.IOLoop.current().stop()


async def run_command(cxn, cmd, args=None):
    args = args if args is not None else {}
    print("Running cmd: {}".format(cmd))
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


if __name__ == '__main__':
    main()
