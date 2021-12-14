import asyncio
import datetime
import poplib
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Tuple, List

import nonebot
import dateutil
from dateutil import parser as parse_date
from lxml import etree
from nonebot.log import logger
from nonebot.utils import run_sync

from .utils import get_advanced
from ..config import Config
from ..model import ParsedObject

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())

newest_mail_time = ""
_last_mail_time = ""
EMAIL_ADDR = plugin_config.mail_recv_addr
PASSWORD = plugin_config.mail_recv_pwd.get_secret_value()
POP3_SERVER = plugin_config.pop3_server
MONI_ADDRS = plugin_config.moni_addrs


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
        for part in parts:
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
    date_cst = date_jst.astimezone(cst).replace(microsecond=0)
    time_stp = str(int(date_cst.timestamp()))   # 时间戳不受时区影响
    date_str = f"时间：{date_cst.year}年{date_cst.month}月{date_cst.day}日 {date_cst.time()}"
    return from_addr, subject_str, date_str, time_stp


def parse_mail_content(raw_content: str) -> ParsedObject:
    root = etree.HTML(raw_content)
    body = root[1]
    content_str = ""
    images_url = []
    for text in body.iter():
        if text.text:
            content_str += text.text + "\n"
        if text.tail:
            content_str += text.tail + "\n"
        if text.tag == "br":
            content_str += "\n"
        if text.tag == "img":
            images_url.append(text.get("src"))

    return ParsedObject(text=content_str, images_url=images_url)


async def download_mail_images(imgs_url: List[str]) -> Tuple[bytes, ...]:
    if imgs_url:
        img_tasks = [get_advanced(url) for url in imgs_url]
        imgs = await asyncio.gather(*img_tasks)
        if None in imgs:
            raise ValueError("没有完整地下载到图片")
        return imgs


@run_sync
def get_latest_mail() -> Tuple[str, List[ParsedObject]]:
    global newest_mail_time
    # 连接到POP3服务器:
    server = poplib.POP3_SSL(POP3_SERVER)
    server.user(EMAIL_ADDR)
    server.pass_(PASSWORD)

    resp, mails, octets = server.list()
    index = len(mails)
    new_mails = []
    _latest_mail_time = ""
    while index:
        logger.debug(f"正在检查第{len(mails) - index + 1}封邮件")
        try:
            _, lines, _ = server.retr(index)  # 获取最新邮件
            server.noop()   # 无实际作用。用于触发部分邮件retr时服务器在末尾返回两次".\r\n"的错误
        except poplib.error_proto:  # 应对未知原因的错误：poplib.error_proto:b '.'
            pass
        msg_content = b'\r\n'.join(lines)
        parser = BytesParser()
        msg = parser.parsebytes(msg_content)
        try:
            addr, subj, tim, timstp = parse_mail_header(msg)
        except dateutil.parser._parser.ParserError:
            continue
        if not _latest_mail_time:
            _latest_mail_time = timstp
        if not newest_mail_time:  # 仅用于初始化
            newest_mail_time = _latest_mail_time
            server.quit()
            return "", []
        if timstp > newest_mail_time and addr in MONI_ADDRS:
            rawcontent = parse_mail_raw_content(msg)
            po = parse_mail_content(rawcontent)
            po.text = f"{tim}\n{subj}\n" + po.text
            po.timestamp = timstp
            new_mails.append(po)
        else:
            if timstp <= newest_mail_time:
                break
        index = index - 1
    server.quit()
    return _latest_mail_time, new_mails


async def check_mail_update() -> List[ParsedObject]:
    global newest_mail_time, _last_mail_time
    timstp, mails = await get_latest_mail()
    if mails:
        logger.warning(f"发现{len(mails)}篇mail更新")
        _last_mail_time = newest_mail_time
        newest_mail_time = timstp
        return mails


async def mail_initial():
    await get_latest_mail()


async def restore_mail_time():
    """
    将lastmailtime恢复到上一次的值。

    用于mail中的图片在PO转Message阶段下载失败时，判定本次获取mail更新失败。
    """
    global newest_mail_time, _last_mail_time
    newest_mail_time = _last_mail_time


@run_sync
def get_mail_list() -> List[ParsedObject]:
    """
    用于获取当前邮箱最近 5 篇Mail，返回Mail编号、时间和标题
    """
    global newest_mail_time
    # 连接到POP3服务器:
    server = poplib.POP3_SSL(POP3_SERVER)
    server.user(EMAIL_ADDR)
    server.pass_(PASSWORD)

    _, mails, _ = server.list()
    index = len(mails)
    mails_list = []
    mail_cnt = 5 + 1

    while index and mail_cnt:
        try:
            _, lines, _ = server.retr(index)  # 获取最新邮件
            server.noop()   # 无实际作用。用于触发部分邮件retr时服务器在末尾返回两次".\r\n"的错误
        except poplib.error_proto:  # 应对未知原因的错误：poplib.error_proto:b '.'
            logger.warning("触发未知错误，已经捕获")
            pass
        logger.debug(f"正在检查第{len(mails) - index + 1}封邮件")
        msg_content = b'\r\n'.join(lines)
        parser = BytesParser()
        msg = parser.parsebytes(msg_content)
        try:
            addr, subj, tim, timstp = parse_mail_header(msg)
        except dateutil.parser._parser.ParserError:
            continue

        if addr in MONI_ADDRS:
            rawcontent = parse_mail_raw_content(msg)
            po = parse_mail_content(rawcontent)
            po.text = f"{tim}\n{subj}"
            po.timestamp = timstp
            mails_list.append(po)
            mail_cnt = mail_cnt - 1
        index = index - 1
    server.quit()
    return mails_list

async def restore_mail_time_manually(timstp: int):
    """
    将最新Mail时间恢复到指定的时间，以获取指定时间之后的Mail
    """
    global newest_mail_time
    newest_mail_time = timstp