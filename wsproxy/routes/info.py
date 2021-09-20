"""routes.py.

Implements the routes for a simple echo (test).
"""
import asyncio
import psutil
from tornado import ioloop
from wsproxy.parser.json import Route, RouteType


async def echo(endpoint, args):
    """Simple test route that echoes the given arguments."""
    await endpoint.next(args)


async def info_subscription(endpoint, args):
    """Sub to get information about the target system."""
    try:
        # Require the sleep time to be at least 1 second.
        sleep_time = min(1, args.get('sleep_time', 5))
    except Exception:
        await endpoint.error('Invalid arguments!')
        return
    while True:
        process = psutil.Process()
        with process.oneshot():
            msg = dict(
                cpu_times=process.cpu_times(),
                cpu_percent=process.cpu_percent(),
                create_time=process.create_time(),
                parent_pid=process.ppid(),
                status=process.status())
        await endpoint.next(msg)

        # Sleep for 5 seconds before sending the next update.
        await asyncio.sleep(sleep_time)


async def count_subscription(endpoint, args):
    """Simple subscription that counts based on the given arguments.

    The 'count' parameter is parsed from 'args' as follows:
     - count < 0: Count upward indefinitely from 1
     - count = 0: Return 0 and exit.
     - count > 0: Count down from the given count to 0, then stop.
    """
    try:
        count = int(args.get('count', 0))
        timeout = args.get('timeout')
    except Exception:
        await endpoint.error("Error")
        return

    while True:
        if timeout:
            await asyncio.sleep(timeout)
        if count < 0:
            await endpoint.next(-count)
        elif count > 0:
            await endpoint.next(count)
        else:
            await endpoint.next(count)
            return
        count -= 1


async def connection_info_subscription(endpoint, args):
    """Subscription that updates everytime the connections change."""
    context = endpoint.state.context
    while True:
        curr_cxn_id = endpoint.state.cxn_id
        result = {
            'connections': {
                str(cxn_id): dict(
                    user=state.auth_manager.get_subject(),
                    url=state.other_url
                )
                for cxn_id, state in context.connection_mapping.items()
            },
            'current_connection': curr_cxn_id
        }
        await endpoint.next(result)
        await context.wait_for_connection_change()


async def garbage_collect_post(endpoint, args):
    """Simple post to request garbage collection."""
    import gc

    result = gc.collect()
    await endpoint.next(u'Garbage collection returned: {}'.format(result))


def get_routes():
    """Return the routes for this module."""
    return [
        Route(RouteType.ONCE, "echo", echo, 'test'),
        Route(RouteType.SUB, "count", count_subscription, 'test'),
        Route(RouteType.SUB, "info", info_subscription, 'system-inspect'),
        Route(RouteType.SUB, "garbage_collect", garbage_collect_post,
              'system-inspect'),
        Route(RouteType.SUB, "connection_info", connection_info_subscription,
              'system-inspect'),
    ]
