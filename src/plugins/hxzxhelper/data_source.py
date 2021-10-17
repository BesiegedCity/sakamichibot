import datetime
import poplib
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Tuple, List, Union, Optional

import httpx
import nonebot
from dateutil import parser as parse_date
from httpx import AsyncClient
from lxml import etree
from nonebot.adapters.cqhttp.message import MessageSegment
from nonebot.log import logger
from nonebot.utils import run_sync

from .config import Config

lastblogtime = ""
lasttwitime = ""
lastmailtime = ""
oldtwiset = set([])
global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
PROXIES = plugin_config.twi_proxies
PARAMS = plugin_config.twi_params
BEARER_TOKEN = plugin_config.twi_bearer_token
TWI_HEADERS = plugin_config.twi_headers


async def get_latest_blog():
    try:
        async with AsyncClient() as client:
            ret = await client.get(f"https://blog.nogizaka46.com/{plugin_config.member_abbr}/atom.xml")
        tree = etree.XML(ret.content)
        return tree
    except httpx.ReadTimeout:
        logger.error("服务器读取超时")
    except httpx.ConnectTimeout:
        logger.error("服务器连接超时")
    except httpx.RequestError:
        logger.exception("下载博客错误")
    raise ValueError("下载到的博客内容为空")


def parse_blog(tree):
    ns = {"ns": "http://www.w3.org/2005/Atom"}
    images = None
    imgcnt = 1

    date = tree.xpath('//ns:entry[1]/ns:published/text()', namespaces=ns)[0]
    text = f"日期：{date[:10]}\n"

    title = tree.xpath('//ns:entry[1]/ns:title/text()', namespaces=ns)[0]
    text += f"标题：{title}\n"

    entry1 = tree.xpath('//ns:entry[1]/ns:content/text()', namespaces=ns)[0]  # XPath中列表下标从1开始
    contenthtml = etree.HTML(entry1)
    lines = contenthtml.xpath("/html/body/div/div[2]/*")
    for line in lines:
        if line.tag == 'p':
            if line.xpath("./text()"):
                text += line.xpath("./text()")[0]  # 有span的text就为None（即使既有span又有text）
            for span in line.getchildren():
                chds = span.getchildren()  # a
                for chd in chds:  # 实际上放在span里面的只有tag为a的超链接
                    if chd.text:
                        if chd.get("href"):
                            text += "【链接】"
                        else:
                            text += "【未知文字】"
                        text += chd.text
                if span.text:  # 莫名其妙被拆成几行span的一句话
                    text += span.text
        else:
            if line.tag == 'div':
                for div in line.getchildren():
                    if div.tag == "div":
                        img = div.getchildren()[0]
                        if img.tag == "img":
                            text += f"【第{imgcnt}张图片的位置】"
                            images += MessageSegment.image(img.get("src"))
                            imgcnt += 1
        text += "\n"
    return [text, images]


def parse_blog_time(tree) -> str:
    ns = {"ns": "http://www.w3.org/2005/Atom"}
    date = tree.xpath('//ns:entry[1]/ns:published/text()', namespaces=ns)[0]
    return date[:10]


def convert_blog2message(blog):
    return parse_blog(blog)


async def check_if_blog_update():
    global lastblogtime
    try:
        latestblog = await get_latest_blog()
        newtime = parse_blog_time(latestblog)
        if newtime != lastblogtime:
            logger.info(f"发现博客更新")
            lastblogtime = newtime
            return convert_blog2message(latestblog)
        else:
            return False
    except ValueError as errmsg:
        logger.error(f"自动获取博客更新失败：{errmsg}")
        return False


async def blog_initial():
    global lastblogtime
    latestblog = await get_latest_blog()
    lastblogtime = parse_blog_time(latestblog)


async def get_latest_twi():
    try:
        async with AsyncClient(proxies=PROXIES, headers=TWI_HEADERS) as client:
            ret = await client.get("https://api.twitter.com/2/users/{}/tweets".format(317684165),  # @nogizaka46
                                   params=PARAMS)
            if ret.status_code != httpx.codes.OK:
                raise httpx.HTTPStatusError
            ret = ret.json()
            return ret
    except httpx.HTTPStatusError:
        logger.error(f"服务器状态码错误：{ret.status_code}")
    except httpx.ReadTimeout:
        logger.error("服务器读取超时")
    except httpx.ProxyError:
        logger.error("代理服务器出错")
    except httpx.RequestError:
        logger.exception("下载推文错误")
    raise ValueError("下载到的推文内容为空")


async def download_twi_img(url: str):
    try:
        async with AsyncClient(proxies=PROXIES) as client:
            ret = await client.get(url)
            ret = ret.content
            return ret
    except httpx.RequestError:
        logger.error("下载推文图片失败")
        raise ValueError("下载推文图片为空")


async def parse_twi(js: dict, update=False):
    msgs = []
    for entry in js["data"]:
        twiid = entry["id"]
        text = entry["text"]
        # if True:    # DEBUG ONLY
        flag_keyword = False
        for keyword in plugin_config.twi_moni_keywords:
            if text.find(keyword) != -1:
                flag_keyword = True
                break
        if flag_keyword or not update:
            if twiid in oldtwiset and update:
                continue  # 当处于自动更新状态时，自动跳过已经发过的推特
            oldtwiset.add(twiid)
            msg = MessageSegment.text("【推特更新】\n@乃木坂46：")
            logger.info(f"当前处理推文：{repr(entry)}")
            if "urls" in entry["entities"]:
                for url in entry["entities"]["urls"]:
                    if url["display_url"].find("pic.twitter.com") == -1 and url["display_url"].find("dlvr.it") == -1:
                        text = text.replace(url["url"], url["display_url"])
                    else:
                        text = text.replace(url["url"], "")
            msg += MessageSegment.text(text)
            if "attachments" in entry:
                if "media_keys" in entry["attachments"]:
                    for media_key in entry["attachments"]["media_keys"]:
                        for item in js["includes"]["media"]:
                            if item["media_key"] == media_key:
                                if item["type"] == "photo":
                                    logger.info(f"推文图片信息：{repr(item)}")
                                    img = await download_twi_img(item["url"])
                                    msg += MessageSegment.image(img)
                                    break
                                if item["type"] == "video":
                                    logger.info(f"推文视频信息：{repr(item)}")
                                    img = await download_twi_img(item["preview_image_url"])
                                    msg += MessageSegment.image(img)
                                    break
            # msg += MessageSegment.text(f"发送时间：{entry['created_at']}")
            msgs.append(msg)
    return msgs


async def convert_twi2message(twi):
    return await parse_twi(twi)


def update_twi_time(js: dict) -> str:
    oldest_id = js["meta"]["oldest_id"]
    logger.info(f"oldtwiset状态：{oldtwiset}")

    for key in oldtwiset.copy():
        if key < oldest_id:
            oldtwiset.remove(key)  # 清除set中时间晚于最后一条推特发送时间的推特

    return js["meta"]["newest_id"]


async def check_if_twi_update():
    global lasttwitime
    try:
        twi = await get_latest_twi()
        newtime = update_twi_time(twi)
        if newtime != lasttwitime:
            logger.info(f"发现推特更新，正在检查是否与设定关键词有关...")
            lasttwitime = newtime
            return await parse_twi(twi, update=True)
        else:
            return False
    except ValueError as errmsg:
        logger.error(f"自动获取最新推文失败：{errmsg}")
        return False


async def twi_initial():
    global lasttwitime
    latesttwi = await get_latest_twi()
    await parse_twi(latesttwi, update=True)  # 空转一次，更新set
    lasttwitime = update_twi_time(latesttwi)


def guess_charset(msg):
    charset = msg.get_charset()
    if charset is None:
        content_type = msg.get('Content-Type', '').lower()
        pos = content_type.find('charset=')
        if pos >= 0:
            charset = content_type[pos + 8:].strip()
    return charset


def decode_str(s):
    value, charset = decode_header(s)[0]
    if charset:
        value = value.decode(charset)
    return value


def parse_mail_raw_content(mail: Message):
    if mail.is_multipart():
        parts = mail.get_payload()
        for part in parts:  # get_payload之后对payload进行遍历
            content_type = part.get_content_type()
            if content_type == 'text/html':
                content = part.get_payload(decode=True)
                charset = guess_charset(part)
                if charset:
                    content = content.decode(charset)
                return content


def parse_mail_header(mail: Message):
    from_raw = mail.get("From" '')
    _, from_addr = parseaddr(from_raw)

    subject_raw = mail.get("Subject", "")
    subject_str = "标题：" + decode_str(subject_raw)

    date_raw = mail.get("Date", "")
    date_jst = parse_date.parse(date_raw)
    cst = datetime.timezone(datetime.timedelta(hours=8))
    date_cst = date_jst.astimezone(cst).replace(second=0, microsecond=0)
    time_stp = str(int(date_cst.timestamp()))
    date_str = f"时间：{date_cst.year}年{date_cst.month}月{date_cst.day}日 " \
               f"{str(date_cst.time())[:-3]}"
    return from_addr, subject_str, date_str, time_stp


def parse_mail_content(raw_content: str):
    root = etree.HTML(raw_content)
    body = root[1]
    content_str = ""
    images_url = []
    for text in body.iter():
        # print("%s - %s" % (text.tag, text.text))
        if text.text:
            content_str += text.text + "\n"
        if text.tag == "br":
            content_str += "\n"
        if text.tag == "img":
            # content_str += MessageSegment.image(text.get("src")) + "\n"
            images_url.append(text.get("src"))
    return content_str, images_url


@run_sync
def get_latest_mail() -> Union[Tuple[None, None, None], Tuple[str, Tuple[Optional[str], ...], str]]:
    email = plugin_config.mail_recv_addr
    password = plugin_config.mail_recv_pwd.get_secret_value()
    pop3_server = plugin_config.pop3_server
    moni_addr = plugin_config.moni_addrs

    # 连接到POP3服务器:
    server = poplib.POP3_SSL(pop3_server)
    server.user(email)
    server.pass_(password)

    resp, mails, octets = server.list()
    index = len(mails)
    while index:
        logger.debug(f"正在检查第{len(mails) - index + 1}封邮件")
        _, lines, _ = server.retr(index)  # 获取最新邮件
        msg_content = b'\r\n'.join(lines)
        parser = BytesParser()
        msg = parser.parsebytes(msg_content)

        addr, subj, tim, timstp = parse_mail_header(msg)
        if addr in moni_addr:
            rawcontent = parse_mail_raw_content(msg)
            content, images_url = parse_mail_content(rawcontent)
            # print(f"{tim}\n{subj}\n{content}")
            server.quit()
            return f"{tim}\n{subj}\n{content}", tuple(images_url), timstp
        else:
            index = index - 1
    server.quit()
    return None, None, None


async def download_mail_images(imgs_url: List) -> Tuple[bytes, ...]:
    images = []
    if imgs_url:
        async with AsyncClient() as client:
            for url in imgs_url:
                try:
                    img = await client.get(url)
                    images.append(img.content)
                except httpx.RequestError:
                    logger.exception("下载mail配图错误：")
                    raise ValueError("下载mail配图错误，mail更新失败")
        return tuple(images)


async def check_if_mail_update() -> Union[Tuple[None, None, None], Tuple[str, Tuple[bytes, ...], str]]:
    global lastmailtime
    content, images_url, timstp = await get_latest_mail()
    if content and timstp > lastmailtime:
        logger.info("发现mail更新")
        try:
            images = await download_mail_images(images_url)
            lastmailtime = timstp
            return content, images, timstp
        except ValueError as errmsg:
            logger.error(errmsg)
    return None, None, None


async def mail_initial():
    global lastmailtime
    _, _, timstp = await get_latest_mail()
    lastmailtime = timstp
