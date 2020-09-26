# -*- coding: utf-8 -*-
"""common.py.

Common code for client and server code.
"""
import copy
import signal
import threading
from functools import partial
from tornado import ioloop, gen
from logger import main_logger

def register_signal_handler(handler):
    """Register the signal handler to stop the given IOLoop."""
    def _handler(signum, stackframe):
        handler()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


class IOLoopService(object):

    def __init__(self, setup_sighandler=True, use_ioloop=None):
        self.lock = threading.Lock()
        self.shutdown_hooks = []
        self.drain_hooks = []
    
        if use_ioloop:
            self.loop = use_ioloop
        else:
            self.loop = ioloop.IOLoop.current()
        
        if setup_sighandler:
            register_signal_handler(partial(self.stop, True))
    
    def add_shutdown_hook(self, hook, *args, **kwargs):
        with self.lock:
            self.shutdown_hooks.append(partial(hook, *args, **kwargs))
    
    def add_ioloop_drain_hook(self, hook, *args, **kwargs):
        with self.lock:
            self.drain_hooks.append(partial(hook, *args, **kwargs))

    def run_forever(self):
        main_logger.info("Starting IOLoop.")
        self.loop.start()
        with self.lock:
            hooks = copy.copy(self.shutdown_hooks)
        main_logger.info("IOLoop stopped. Running %d shutdown hooks.", len(hooks))
        self.loop.close()
        # Run any shutdown hooks as well, in reverse order.
        #
        # NOTE: This is done after 'self.loop.start()' exits, because this is
        # when we know the IOLoop is closed.
        for hook in reversed(hooks):
            try:
                hook()
            except Exception:
                main_logger.exception("Error processing shutdown hook: %s", hook)

    async def _stop(self):
        """Run the drain hooks and stop the IOLoop.
        
        The shutdown hooks will be run after the IOLoop stops.
        """
        # Run the drain hooks.
        with self.lock:
            drain_hooks = copy.copy(self.drain_hooks)
        main_logger.info("Running %d drain hooks before shutting down IOLoop.", len(drain_hooks))
        
        for hook in drain_hooks:
            try:
                await hook()
            except Exception:
                main_logger.exception("Error running drain hook!")
        self.loop.stop()

    def stop(self, from_signal=False):
        """Stop this IOLoopService from running.
        
        Use 'from_signal=True' when calling this from a signal handler.
        """
        if from_signal:
            self.loop.add_callback_from_signal(self._stop)
        else:
            self.loop.add_callback(self._stop)
