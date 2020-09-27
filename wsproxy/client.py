#!/usr/bin/python
# -*- coding: utf-8 -*-
"""client.py.

Client-side websocket connection that connects to the WS server
and tunnels the socket accordingly.
"""
import sys
import socket
import asyncio
from tornado import websocket, iostream, httpclient
from tornado.ioloop import IOLoop
from context import ClientContext
from common import IOLoopService
from logger import get_child_logger, setup_default_logger


logger = get_child_logger("client")


# class LocalClientConnection(object):
# 
#     def __init__(self, client_url):
#         self.client_url = client_url
# 
#     @property
#     def client_port(self):
#         try:
#             return int(self.client_url.split(':')[1])
#         except Exception:
#             # Assume port 80 by default.
#             return 80
#     
#     @property
#     def client_host(self):
#         try:
#             return self.client_url.split(':', 1)[0] or 'localhost'
#         except Exception:
#             return 'localhost'
# 
#     async def run(self):
#         try:
#             # Open the socket to read/write to the local server, using any port.
#             s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
#             self.local_stream = iostream.IOStream(s)
#             
#             # Connect to the local stream.
#             domain_tuple = (self.client_host, self.client_port)
#             await self.local_stream.connect(domain_tuple)
#         except ConnectionError:
#             logger.exception("Error connecting to local stream!")


class TunnelClientConnection(object):

    def __init__(self, tunnel_url):
        self.tunnel_url = tunnel_url
        self.context = ClientContext()
        self.connection = None
    
    def is_connected(self):
        return bool(self.connection)
    
    def on_message(self, msg):
        IOLoop.current().add_callback(self.process_message, msg)
    
    async def process_message(self, msg):
        logger.info("Received: %s", msg)
        # This denotes that the connection was closed.
        if not msg:
            self.close()
            return

        # Parse the message
        opcode = msg[0]
        
        # Open a new connection to the given port.
        if opcode == 0:
            _, msg_id, port, stm_id = struct.unpack('!BIHI')
            await self.context.open_tcp_stream(port, stm_id)
            
            # If we get here, the result was successful, so return as such.
            data = struct.pack('!BIB', 0, msg_id, 0)
            await self.connection.write_message(data)
        
        if opcode == 1:
            _, stm_id = struct.unpack('!BI', msg[:5])
            stm = self.context.get_stream(stm_id)
            if not stm:
                return

    async def run(self):
        try:
            self.connection = await websocket.websocket_connect(
                self.tunnel_url, on_message_callback=self.on_message,
                connect_timeout=10, compression_options={},
                ping_interval=5, ping_timeout=5, max_message_size=100)

            while True:
                await asyncio.sleep(10)
        except ConnectionError:
            logger.exception("Could not connect to tunnel!")
            self.connection.close()
    
    def close(self):
        self.connection.close()
        self.connection = None


# class ClientProxy(IOLoopService):
# 
#     def __init__(self, tunnel_url, client_url, client_id=None, **kwargs):
#         super(ClientProxy, self).__init__(**kwargs)
#         self.tunnel = TunnelClientConnection(tunnel_url)
#         self.loop.add_callback(self.tunnel.run)
# 
#     @property
#     def client_port(self):
#         try:
#             return int(self.client_url.split(':')[1])
#         except Exception:
#             # Assume port 80 by default.
#             return 80
#     
#     @property
#     def client_host(self):
#         try:
#             return self.client_url.split(':', 1)[0] or 'localhost'
#         except Exception:
#             return 'localhost'
#     
#     async def connect_local(self):
#         try:
#             # Open the socket to read/write to the local server, using any port.
#             s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
#             self.local_stream = iostream.IOStream(s)
#             
#             # Connect to the local stream.
#             domain_tuple = (self.client_host, self.client_port)
#             logger.info("DOMAIN: %s", domain_tuple)
#             await self.local_stream.connect(domain_tuple)
#         except ConnectionError:
#             logger.exception("Error connecting to local stream!")

class ClientService(IOLoopService):

    def __init__(self, server_url, **kwargs):
        super(ClientService, self).__init__(**kwargs)
        self.server_url = server_url
    

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
