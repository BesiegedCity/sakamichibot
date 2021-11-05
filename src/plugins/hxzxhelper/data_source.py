import asyncio
from typing import Tuple, List, Union

import nonebot
from nonebot.adapters.cqhttp.message import Message, MessageSegment

from .config import Config
from .lib.blog import check_blog_update, get_blog_f
from .lib.mail import check_mail_update
from .lib.twitter import check_tweet_update, get_tweets_f
from .lib.utils import get_advanced
from .model import ParsedObject, Mail

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
PROXIES = plugin_config.proxies


async def _download_image(url: str) -> bytes:
    ret = None
    if PROXIES:  # 优先通过代理下载，失败时自动转为直连下载
        ret = await get_advanced(url, proxies=PROXIES)
    if not ret:
        ret = await get_advanced(url)
    if ret:
        ret = ret.content
        return ret
    else:
        raise ValueError("下载到的图片为空")


async def parse_po2msg(po: ParsedObject) -> MessageSegment:
    msg = MessageSegment.text(po.text)
    if po.images_url:
        img_tasks = [_download_image(url) for url in po.images_url]
        imgs = await asyncio.gather(*img_tasks)
        if None in imgs:
            raise ValueError("没有完整地下载到图片")
        img_msgs = [MessageSegment.image(img) for img in imgs]
        msg += img_msgs
    return msg


async def get_blog_update() -> Union[Message, MessageSegment]:
    po = await check_blog_update()
    if po:
        msg = await parse_po2msg(po)
        return msg


async def get_blog_manually() -> Union[Message, MessageSegment]:
    po = await get_blog_f()
    if po:
        msg = await parse_po2msg(po)
        return msg


async def get_mail_update() -> List[Mail]:
    pos = await check_mail_update()
    if pos:
        mails = []
        for po in pos:
            imgs = []
            if po.images_url:
                img_tasks = [_download_image(url) for url in po.images_url]
                imgs = await asyncio.gather(*img_tasks)
                if None in imgs:
                    raise ValueError("没有完整地下载到图片")

            m = Mail()
            m.raw_text = po.text
            m.images = imgs
            m.time = int(po.timestamp)
            m.stat = 1
            mails.append(m)
        return mails


async def get_tweet_update() -> Tuple[MessageSegment, ...]:
    pos = await check_tweet_update()
    if pos:
        tweet_tasks = [parse_po2msg(po) for po in pos]
        tweets_msgs = await asyncio.gather(*tweet_tasks)
        return tweets_msgs


async def get_tweet_manually() -> Tuple[MessageSegment, ...]:
    pos = await get_tweets_f()
    if pos:
        tweet_tasks = [parse_po2msg(po) for po in pos]
        tweets_msgs = await asyncio.gather(*tweet_tasks)
        return tweets_msgs
