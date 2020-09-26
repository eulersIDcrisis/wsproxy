"""logger.py.

Main logging utilities for WS Proxy.
"""
import logging


main_logger = logging.getLogger('wsproxy')


def setup_default_logger():
    main_logger.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    main_logger.addHandler(stream_handler)


def get_child_logger(name):
    return main_logger.getChild(name)
