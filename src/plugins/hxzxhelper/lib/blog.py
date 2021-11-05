import datetime
import re
from typing import Union

import nonebot
from dateutil import parser
from lxml import etree
from nonebot.log import logger

from .utils import get_advanced
from ..config import Config
from ..model import ParsedObject

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())

lastblogtime = ""


def parse_blog(blog: Union[bytes, str]) -> ParsedObject:
    tree = etree.XML(blog)
    ns = {"ns": "http://www.w3.org/2005/Atom"}
    images = []
    imgcnt = 1

    date = tree.xpath('//ns:entry[1]/ns:published/text()', namespaces=ns)[0]
    title = tree.xpath('//ns:entry[1]/ns:title/text()', namespaces=ns)[0]
    entry1 = tree.xpath('//ns:entry[1]/ns:content/text()', namespaces=ns)[0]  # XPath中列表下标从1开始

    text = f"日期：{date[:10]}\n" \
           f"标题：{title}\n"

    contenthtml = etree.HTML(entry1)
    text += "\n"
    for element in contenthtml.iter():
        if element.tag == "p" and text[-1] != "\n":
            text += "\n"
        if element.text:
            text += element.text
        if element.tail:
            text += element.tail
        if element.tag == "img":
            text += f"【第{imgcnt}张图片的位置】"
            images.append(element.get("src"))
            imgcnt += 1
        if element.tag == "br":
            text += "\n" if text[-1] == "\n" else "\n\n"
    text = re.sub(r"^\s*", "", text).strip("\n")
    return ParsedObject(text=text, images_url=images)


def parse_blog_time(blog: Union[bytes, str]) -> datetime.datetime:
    tree = etree.XML(blog)
    ns = {"ns": "http://www.w3.org/2005/Atom"}
    date = tree.xpath('//ns:entry[1]/ns:published/text()', namespaces=ns)[0]
    return parser.parse(date)


async def download_latest_blog() -> bytes:
    ret = await get_advanced(f"https://blog.nogizaka46.com/{plugin_config.member_abbr}/atom.xml")
    if ret:
        return ret.content
    else:
        raise ValueError("下载到的博客内容为空")


async def check_blog_update() -> ParsedObject:
    global lastblogtime
    try:
        latestblog = await download_latest_blog()
        newtime = parse_blog_time(latestblog)
        if newtime > lastblogtime:
            logger.info(f"发现博客更新")
            lastblogtime = newtime
            blog = parse_blog(latestblog)
            return blog
    except ValueError as errmsg:
        logger.error(f"自动获取博客更新失败：{errmsg}")


async def get_blog_f() -> ParsedObject:
    global lastblogtime
    try:
        latestblog = await download_latest_blog()
        newtime = parse_blog_time(latestblog)
        lastblogtime = newtime
        blog = parse_blog(latestblog)
        if newtime > lastblogtime:
            lastblogtime = newtime
        return blog
    except ValueError as errmsg:
        logger.error(f"自动获取博客更新失败：{errmsg}")


async def blog_initial():
    global lastblogtime
    latestblog = await download_latest_blog()
    lastblogtime = parse_blog_time(latestblog)
