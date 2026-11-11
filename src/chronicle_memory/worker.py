"""Standalone time-check worker; CLI and MCP processes do not spawn it."""
import asyncio
import logging

logger=logging.getLogger(__name__)


async def run_worker(service,interval,stop,emit):
    if interval<=0:
        raise ValueError('检查间隔必须大于 0')
    while not stop.is_set():
        processed=[]
        try:
            processed=await service.database(service.store.tick)
            if processed:
                emit({'ok':True,'result':{'processed':processed}})
        except Exception:
            logger.exception('时间检查失败，将在下个周期重试')
        if processed:
            # Each batch releases the namespace write lock before taking more.
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.wait_for(stop.wait(),timeout=interval)
        except TimeoutError:
            pass
