# -*- coding: utf-8 -*-
"""common.py.

Common code for client and server code.
"""
import copy
import signal
import logging
import threading
from functools import partial
from tornado import ioloop, gen


main_logger = logging.getLogger('wsproxy')


def setup_default_logger():
    main_logger.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    main_logger.addHandler(stream_handler)


def get_child_logger(name):
    return main_logger.getChild(name)


def register_signal_handler(handler):
    """Register the signal handler to stop the given IOLoop."""
    def _handler(signum, stackframe):
        handler()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


class IOLoopService(object):

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
