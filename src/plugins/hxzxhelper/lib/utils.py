from typing import Union

import httpx
from httpx import AsyncClient
from nonebot.log import logger


async def get_advanced(url: str, params=None, headers=None, proxies=None) -> Union[None, httpx.Response]:
    """
        对异步 httpx.get() 方法进行再封装，加入了自动重试和错误捕获。

    :param url:
    :param params:
    :param headers:
    :param proxies:
    :return: None or httpx.Response object
    """
    retry = 5
    async with AsyncClient(proxies=proxies, headers=headers) as client:
        while retry:
            retry = retry - 1
            try:
                ret = await client.get(url, params=params)
                if ret.status_code != httpx.codes.OK:
                    logger.debug(ret.text)
                    ret.raise_for_status()
                return ret
            except httpx.HTTPStatusError:
                logger.warning(f"服务器状态码错误：{ret.status_code}")
            except httpx.ConnectTimeout:
                logger.warning("服务器连接超时")
            except httpx.ReadTimeout:
                logger.warning("服务器读取超时")
            except httpx.ProxyError:
                logger.warning("代理服务器出错")
            except httpx.RequestError:
                logger.exception("网络错误")
        else:
            logger.error("所有Get尝试均失败，返回None")
            return None
