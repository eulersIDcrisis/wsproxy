"""routes.py

Implements the routes for a simple echo (test).
"""
import asyncio
import psutil
from tornado import ioloop
from wsproxy.parser.json import Route, RouteType


async def echo(endpoint, args):
    await endpoint.next(args)


async def info_subscription(endpoint, args):
    """Sub to get information about the target system."""
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
        await asyncio.sleep(5)


async def count_subscription(endpoint, args):
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
                    url=state.other_url
                )
                for cxn_id, state in context.connection_mapping.items()
            },
            'current_connection': curr_cxn_id
        }
        await endpoint.next(result)
        await context.wait_for_connection_change()


def get_routes():
    return [
        Route(RouteType.ONCE, "echo", echo, 'test'),
        Route(RouteType.SUB, "count", count_subscription, 'test'),
        Route(RouteType.SUB, "info", info_subscription, 'system-inspect'),
        Route(RouteType.SUB, "connection_info", connection_info_subscription, 'system-inspect'),
    ]
