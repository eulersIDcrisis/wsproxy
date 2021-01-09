#!/usr/bin/python
# -*- coding: utf-8 -*-
"""client.py.

Client-side websocket connection that connects to the WS server
and tunnels the socket accordingly.
"""
import sys
import socket
import asyncio
from tornado import websocket, iostream, httpclient, gen
from tornado.ioloop import IOLoop

from wsproxy.base import common
from wsproxy.base.common import client_logger as logger
from wsproxy.base.client_util import WsClientConnection
from wsproxy.echo.routes import EchoContext


class ClientService(common.IOLoopService):

    def __init__(self, server_url, **kwargs):
        super(ClientService, self).__init__(**kwargs)
        self.server_url = server_url
        self.ioloop.add_callback(self.run_main_loop)

    async def run_main_loop(self):
        while True:
            connected = await self._run_connection()
            # If the connection never occurred, stall for 5 seconds.
            if not connected:
                await asyncio.sleep(5)
                continue

        logger.info("Stopping main loop.")

    async def _run_connection(self):
        try:
            context = EchoContext()
            cxn = WsClientConnection(context, self.server_url)
            try:
                res = await cxn.open()
            except Exception as exc:
                logger.warning("Could not connect %s -- Reason: %s", self.server_url, str(exc))
                return False
            await cxn.run()
        except Exception:
            logger.exception("Error in run_main_loop()")


def main():
    if len(sys.argv) < 2:
        print("Usage: {} <server_url>".format(sys.argv[0]))
        return
    server_url = sys.argv[1]
    
    common.setup_default_logger()
    try:
        service = ClientService(server_url)
        service.run_ioloop()
    except Exception:
        logger.exception("Error in program!")


if __name__ == "__main__":
    main()
