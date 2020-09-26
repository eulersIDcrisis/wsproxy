#!/usr/bin/python
# -*- coding: utf-8 -*-
"""client.py.

Client-side websocket connection that connects to the WS server
and tunnels the socket accordingly.
"""
import sys
import socket
import asyncio
from tornado import gen, websocket, iostream, httpclient
from common import IOLoopService
from logger import get_child_logger, setup_default_logger
from _threading_local import local


logger = get_child_logger("client")


class ClientProxy(IOLoopService):

    def __init__(self, tunnel_url, client_url, **kwargs):
        super(ClientProxy, self).__init__(**kwargs)
        self.tunnel_url = tunnel_url
        
        # 'client_url' stores the local host:port field, and
        # 'local_stream' stores the socket stream itself.
        self.client_url = client_url
        self.local_stream = None
        
        self.loop.add_callback(self._init_connections)

    @property
    def client_port(self):
        try:
            return int(self.client_url.split(':')[1])
        except Exception:
            # Assume port 80 by default.
            return 80
    
    @property
    def client_host(self):
        try:
            return self.client_url.split(':', 1)[0] or 'localhost'
        except Exception:
            return 'localhost'
    
    async def _init_connections(self):
        try:
            await self.connect_local()
            await self.connect_tunnel()
        except Exception:
            logger.exception("Failed to initialize connections!")
            self.stop()

    async def connect_local(self):
        try:
            # Open the socket to read/write to the local server, using any port.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
            self.local_stream = iostream.IOStream(s)
            
            # Connect to the local stream.
            domain_tuple = (self.client_host, self.client_port)
            logger.info("DOMAIN: %s", domain_tuple)
            await self.local_stream.connect(domain_tuple)
        except ConnectionError:
            logger.exception("Error connecting to local stream!")

    async def connect_tunnel(self):
        try:
            connection = await websocket.websocket_connect(
                self.tunnel_url, connect_timeout=10, compression_options={},
                ping_interval=5, ping_timeout=5, max_message_size=100)
            while True:
                msg = await connection.read_message()
                if msg is None:
                    return
                client = httpclient.AsyncHTTPClient()
        except ConnectionError:
            logger.exception("Could not connect to tunnel!")


def main():
    if len(sys.argv) < 3:
        print("Usage: {} <tunnel_url> <local_url>".format(sys.argv[0]))
        return
    tunnel_url = sys.argv[1]
    local_url = sys.argv[2]
    
    setup_default_logger()
    try:
        proxy = ClientProxy(tunnel_url, local_url)
        proxy.run_forever()
    except Exception:
        logger.exception("Error in program!")


if __name__ == "__main__":
    main()
