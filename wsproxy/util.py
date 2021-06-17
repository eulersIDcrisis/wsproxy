"""util.py.

Utilities for managing connections.
"""

import uuid
import signal
import logging
import weakref
from functools import partial
from tornado import tcpserver, ioloop

main_logger = logging.getLogger('wsproxy')


def setup_default_logger(handlers, level=logging.INFO):
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in handlers:
        root_logger.addHandler(handler)


def get_child_logger(name):
    return main_logger.getChild(name)


def get_hostname():
    try:
        hostname = socket.gethostname()
    except Exception:
        return "(not found)"
    try:
        ipaddr = socket.gethostbyaddr(hostname)
        if ipaddr:
            return u'{}-{}'.format(hostname, ipaddr)
    except Exception:
        pass
    return hostname


async def pipe_stream(stm1, stm2, buffer_size=2 ** 16):
    buff = bytearray(buffer_size)
    while True:
        count = await stm1.read_into(buff, partial=True)
        await stm2.write(memoryview(buff)[0:count])


def register_signal_handler(handler):
    """Register the signal handler to stop the given IOLoop."""
    def _handler(signum, stackframe):
        handler()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


class IOLoopContext(object):

    def __init__(self, setup_sighandler=True, use_ioloop=None):
        self.shutdown_hooks = []
        self.drain_hooks = []
    
        if use_ioloop:
            self._loop = use_ioloop
        else:
            self._loop = ioloop.IOLoop.current()
        
        if setup_sighandler:
            register_signal_handler(partial(self.stop, True))
    
    @property
    def ioloop(self):
        return self._loop

    def add_shutdown_hook(self, hook, *args, **kwargs):
        self.shutdown_hooks.append(partial(hook, *args, **kwargs))
    
    def add_ioloop_drain_hook(self, hook, *args, **kwargs):
        self.drain_hooks.append(partial(hook, *args, **kwargs))

    def run_ioloop(self):
        main_logger.info("Starting IOLoop.")
        self.ioloop.start()
        self.ioloop.close()
        main_logger.info("IOLoop stopped. Running %d shutdown hooks.",
                         len(self.shutdown_hooks))
        # Run any shutdown hooks as well, in reverse order.
        #
        # NOTE: This is done after 'self.ioloop.start()' exits, because this is
        # when we know the IOLoop is closed.
        for hook in reversed(self.shutdown_hooks):
            try:
                hook()
            except Exception:
                main_logger.exception("Error processing shutdown hook: %s", hook)
        self.shutdown_hooks = []

    async def _stop(self):
        """Run the drain hooks and stop the IOLoop.
        
        The shutdown hooks will be run after the IOLoop stops.
        """
        # Run the drain hooks.
        main_logger.info("Running %d drain hooks before shutting down IOLoop.",
                         len(self.drain_hooks))
        
        for hook in self.drain_hooks:
            try:
                await hook()
            except Exception:
                main_logger.exception("Error running drain hook!")
        self.drain_hooks = []
        self.ioloop.stop()

    def stop(self, from_signal=False):
        """Stop this IOLoopService from running.
        
        Use 'from_signal=True' when calling this from a signal handler.
        """
        if from_signal:
            self.ioloop.add_callback_from_signal(self._stop)
        else:
            self.ioloop.add_callback(self._stop)


class LocalTcpServer(tcpserver.TCPServer):

    def __init__(self, port, server_id=None):
        super(LocalTcpServer, self).__init__()
        self._port = port
        self._stream_mapping = {}
        self._next_id = 1

    @property
    def port(self):
        return self._port

    def setup(self):
        # We _could_ handle the binding more directly. But this works for now.
        self.listen(self.port)

    def teardown(self):
        """Stop receiving new connections, then close any outstanding ones."""
        self.stop()

    async def handle_stream(self, stream, address):
        stream_id = self._next_id
        self._next_id += 1
        try:
            self._stream_mapping[stream_id] = stream
            await self._handle_stream(stream, address)
        finally:
            self._stream_mapping.pop(stream_id, None)

    async def _handle_stream(self, stream, address):
        raise NotImplementedError("Override in a subclass.")
