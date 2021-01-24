"""debug.py.

Debug setting utilities when running unittests.
"""
import logging
import unittest


GLOBAL_DEBUG = 0


def get_unittest_debug():
    return GLOBAL_DEBUG


def enable_debug(debug=1):
    global GLOBAL_DEBUG
    logging.basicConfig(level=logging.DEBUG)

    GLOBAL_DEBUG = debug
