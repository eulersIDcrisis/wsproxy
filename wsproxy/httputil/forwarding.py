"""forwarding.py.

Set of HTTP handlers for managing/creating tunnels for specific ports.
"""
from wsproxy.util import logger
from tornado import web


class TunnelPortHandler(web.RequestHandler):
	"""Handler that manages tunneling a port between the server and client."""

	async def post(self, cxn_id_str):
		pass

	async def get(self, cxn_id_str):
		pass

	async def delete(self, cxn_id_str):
		pass


class ClientInfoHandler(web.RequestHandler):

    def initialize(self, context=None):
        self.context = context
    
    async def get(self, cxn_id):
        try:
            state = self.context.find_state_for_cxn_id(cxn_id)
            if not state:
                self.set_status(404)
                self.write(dict(status=404, message="Not Found"))
                return
            # Send a request to the client to get the info for the platform.
            res = await once(state, 'info', None)
            res['ip_address'] = state.other_url
            self.write(res)
        except Exception:
            logger.exception("Error with client")
            self.set_status(500, "Internal Error")
        finally:
            await self.finish()
