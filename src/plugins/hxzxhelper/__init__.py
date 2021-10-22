import asyncio
import datetime
import re
from io import BytesIO
from typing import List, Union

import apscheduler.jobstores.base
import nonebot
from PIL import Image
from aiohttp.client_exceptions import ServerDisconnectedError
from bilibili_api import dynamic
from bilibili_api.exceptions import ResponseCodeException
from httpx import AsyncClient
from nonebot import on_command, on_startswith, on_message, get_driver
from nonebot.adapters import Bot, Event
from nonebot.adapters.cqhttp.event import GroupMessageEvent
from nonebot.adapters.cqhttp.message import MessageSegment
from nonebot.log import logger
from nonebot.rule import Rule
from nonebot.typing import T_State

from .config import Config
from .data_source import check_if_blog_update, convert_blog2message, blog_initial, get_latest_blog
from .data_source import check_if_mail_update, mail_initial
from .data_source import check_if_twi_update, convert_twi2message, twi_initial, get_latest_twi
from .model import Mail

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
SENDERS = plugin_config.fansub_senders
MASTERS = plugin_config.fansub_masters
ADMINGROUPS = plugin_config.fansub_groups
TIME_WAITBEFORESEND = plugin_config.time_waitbeforesend
TIME_WAITFORIMAGES = plugin_config.time_waitforimages
TIME_CHECKBLOGUPDATE = plugin_config.time_checkblogupdate
TIME_CHECKTWIUPDATE = plugin_config.time_checktwiupdate
TIME_CHECKMAILUPDATE = plugin_config.time_checkmailupdate

maillist: List[Mail] = []
imagelist: List[BytesIO] = []
mail_loadingimg: Union[Mail, None] = None
cred = plugin_config.cred
push_group = 0
scheduler = nonebot.require("nonebot_plugin_apscheduler").scheduler
driver = get_driver()


@driver.on_startup
async def initial():  # 初始化必须成功，否则第一次获取博客和推特更新时会有bug
    global cred, push_group
    if plugin_config.debug:
        logger.info("当前处于开发环境")
        push_group = 0
    else:
        logger.info("当前处于生产环境")
        push_group = 1

    await asyncio.gather(blog_initial(), twi_initial(), mail_initial())
    logger.info("博客、推特、Mail更新组件初始化完毕")


def parse_time(timestr: str):
    year = re.search(r"\d{4}年", timestr)
    month = re.search(r"\d{1,2}月", timestr)
    day = re.search(r"\d{1,2}日", timestr)
    hournminute = re.search(r"\d{1,2}[:：]\d{1,2}", timestr)
    if year and month and day and hournminute:
        hournminute_str = hournminute.group()
        hournminute = hournminute_str.split("：") if len(hournminute_str.split(":")) == 1 else hournminute_str.split(":")
        tm = datetime.datetime(year=int(year.group()[:-1]), month=int(month.group()[:-1]), day=int(day.group()[:-1]),
                               hour=int(hournminute[0]), minute=int(hournminute[1]))
        return tm.timestamp()
    else:
        raise ValueError("导入时间信息出错：年月日时分信息存在缺失")


async def checkifmastergroup(bot: Bot, event: Event, state: T_State) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.group_id in ADMINGROUPS:
        return True
    else:
        return False


async def checkifmaster(bot: Bot, event: Event, state: T_State) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.get_user_id() in MASTERS and event.group_id in ADMINGROUPS:
        return True
    else:
        return False


async def checkifsender(bot: Bot, event: Event, state: T_State):
    if not isinstance(event, GroupMessageEvent):
        return False
    if event.get_user_id() in SENDERS and event.group_id in ADMINGROUPS:
        return True
    else:
        return False


async def checkifnotsender(bot: Bot, event: Event, state: T_State):
    if not isinstance(event, GroupMessageEvent):
        return False
    if not (event.get_user_id() in SENDERS) and event.group_id in ADMINGROUPS:
        return True
    else:
        return False


async def checkifmailimage(bot: Bot, event: Event, state: T_State):
    if mail_loadingimg is None:
        return False
    if not isinstance(event, GroupMessageEvent) or not event.get_user_id() in SENDERS:
        return False
    msg = event.get_message()
    if len(msg) != 1 or msg[0].type != "image":
        return False
    async with AsyncClient() as client:
        img = await client.get(msg[0].data["url"])
    img_check = Image.open(BytesIO(img.content))
    if img_check.width == 960 or img_check.height == 1280 or img_check.height == 720:
        state["img"] = img.content
        return True
    else:
        return False


async def checkifreply(bot: Bot, event: Event, state: T_State):
    if isinstance(event, GroupMessageEvent):
        if event.reply and event.to_me:
            return True
        else:
            return False


load_mail = on_startswith("时间", rule=checkifsender, priority=5)
# 检测到“时间”开头的消息后，等一分钟用于收集配图，一分钟内若遇到第二个以“时间”开头的消息，则立即停止前一个消息的图片收集。
load_trans = on_startswith("时间", rule=checkifnotsender, priority=6)
# 当非消息提供者（not SENDERS）发送以“时间”开头的消息时，认为消息内容是之前mail的翻译。需要进一步匹配是之前哪一条mail的翻译。
load_img = on_message(rule=checkifmailimage, priority=5)
show_tasks = on_command("发送队列", rule=checkifmaster, priority=4)
cancel_task = on_command("取消发送", rule=checkifmastergroup, priority=4)
get_blog = on_command("最新博客", priority=5)
get_twi = on_command("最新推文", priority=5)
send_by_reply = on_message(rule=Rule(checkifreply) & checkifmastergroup, priority=15)


async def send2bili(mail: Mail, event: GroupMessageEvent):
    retry = 6
    bot = nonebot.get_bot(str(event.self_id))
    rsps = {}
    mailindex = maillist.index(mail)
    logger.info(f"正在发送b站动态，序号：{mail.no}，文字内容：{mail.translation}")
    try:
        sendrsps = await dynamic.send_dynamic(f"{plugin_config.dynamic_topic}\n" + mail.translation,
                                              image_streams=mail.images,
                                              credential=cred)
        while retry:
            try:
                await asyncio.sleep(5)  # 等待几秒后再检查审核状态
                dy = dynamic.Dynamic(sendrsps["dynamic_id_str"], credential=cred)
                rsps = await dy.get_info()
                logger.info(f"发送动态结果查询：{rsps}")
                if "desc" in rsps:
                    if "acl" in rsps["desc"]:
                        if rsps["desc"]["acl"] != 0:
                            await bot.send(event, f"mail[{mail.no}]：发送成功（进入审核队列）")
                        else:
                            await bot.send(event, f"mail[{mail.no}]：发送成功（b站已发）")
                    else:
                        await bot.send(event, f"mail[{mail.no}]：发送成功（b站已发）")
                maillist.pop(mailindex)
                return
            except ServerDisconnectedError as errmsg:
                retry = retry - 1
                if retry:
                    logger.error(f"检查动态发送状态出错：{errmsg}, 第{6 - retry}次重试...")
                continue
        else:
            logger.error("五次重试均失败，放弃状态检查")
            await bot.send(event, f"mail[{mail.no}]：发送完毕（状态未知）")
    except ResponseCodeException as errmsg:
        await bot.send(event, f"mail[{mail.no}]：发送失败，{errmsg}")


@show_tasks.handle()
async def showmails(bot: Bot, event: GroupMessageEvent):
    if maillist:
        for mail in maillist:
            await show_tasks.send(mail.info())
    else:
        await show_tasks.finish("处理队列为空")
    await show_tasks.finish()


@cancel_task.handle()
async def canceltask(bot: Bot, event: GroupMessageEvent):
    index = -1
    arg = str(event.get_message()).strip(" ")
    if arg and arg.isdecimal():
        try:
            for mail in maillist:
                if mail.no == int(arg):
                    index = maillist.pop(maillist.index(mail)).no
                    break
            if index == -1:
                raise IndexError
            scheduler.remove_job(arg)
            await cancel_task.finish(f"mail[{arg}]：已取消发送")
        except IndexError:
            await cancel_task.finish("没有在处理和发送队列中找到对应mail")
        except apscheduler.jobstores.base.JobLookupError:
            await cancel_task.finish(f"mail[{arg}]：尚未进入发送队列，已从处理队列中移出")
    else:
        await cancel_task.finish("请提供取消发送的mail数字序号")


@load_img.handle()
async def loadimg(bot: Bot, event: GroupMessageEvent, state: T_State):
    # msg = event.get_message()[0]
    if state["img"]:
        imagelist.append(state["img"])
        logger.info("成功缓存一张mail图片")
        # await load_img.send("成功缓存一张mail图片")


async def loadimg_finish(event: GroupMessageEvent):
    global mail_loadingimg, imagelist
    bot = nonebot.get_bot(str(event.self_id))
    index = maillist.index(mail_loadingimg)
    for img in imagelist:
        maillist[index].images.append(img)
    imagelist = []
    logger.info(f"mail[{maillist[index].no}]：配图收集结束，共收集到{len(maillist[index].images)}张图片")
    # await bot.send(event, f"mail[{maillist[index].no}]：图片收集完成")
    if maillist[index].stat == 2:
        maillist[index].stat = 3
        scheduler.add_job(send2bili, trigger="date",
                          run_date=datetime.datetime.now() + datetime.timedelta(minutes=TIME_WAITBEFORESEND),
                          args=(maillist[index], event), id=str(maillist[index].no))
        await bot.send(event, maillist[index].preview())
    else:
        maillist[index].stat = 1
    mail_loadingimg = None
    return


@load_mail.handle()
async def loadmail(bot: Bot, event: GroupMessageEvent, state: T_State):
    global mail_loadingimg, mailcnt
    if mail_loadingimg is not None:
        scheduler.reschedule_job("loadimages", trigger=None)
        await asyncio.sleep(2)
    mail = Mail()
    raw_msg = str(event.get_message())
    if raw_msg.find("\r\n") == -1:
        firstlineend = raw_msg.find("\n")
    else:
        firstlineend = raw_msg.find("\r\n")
    try:
        mail.time = int(parse_time(raw_msg[:firstlineend]))
    except ValueError as errmsg:
        logger.error(errmsg)
        await load_mail.finish()
    mail.raw_text = str(event.get_message()).strip(" ")
    maillist.append(mail)
    mail_loadingimg = mail
    scheduler.add_job(loadimg_finish, trigger="date",
                      run_date=datetime.datetime.now() + datetime.timedelta(minutes=TIME_WAITFORIMAGES),
                      args=(event,), id="loadimages")
    logger.info(f"mail[{mail.no}]：正在收集配图，时间{TIME_WAITFORIMAGES}分钟")
    # await load_mail.finish(f"mail[{mail.no}]：正在收集配图，时间{TIME_WAITFORIMAGES}分钟")


@load_trans.handle()
async def loadtrans(bot: Bot, event: GroupMessageEvent, state: T_State):
    targetmail = -1
    raw_msg = str(event.get_message())
    logger.info("收集到的翻译:" + repr(raw_msg))
    if raw_msg.find("\r\n") == -1:
        firstlineend = raw_msg.find("\n")
    else:
        firstlineend = raw_msg.find("\r\n")
    try:
        transtime = int(parse_time(raw_msg[:firstlineend]))
        for mail in maillist:
            if mail.time == transtime:
                targetmail = maillist.index(mail)
                break
        if targetmail == -1:
            raise IndexError
    except ValueError as errmsg:
        logger.error(errmsg)
        await load_mail.finish()
    except IndexError:
        logger.error("没有在队列中找到与时间相匹配的mail")
        # await load_trans.finish("没有在队列中找到与时间相匹配的mail")
        await load_mail.finish()
    if maillist[targetmail].translation != "":
        logger.info(f"mail[{maillist[targetmail].no}]：翻译已覆盖")
        await load_trans.send(f"mail[{maillist[targetmail].no}]：翻译已覆盖")
    maillist[targetmail].translation = raw_msg
    logger.info(f"mail[{maillist[targetmail].no}]：翻译已收集")
    # await load_trans.send(f"mail[{targetmail}]：翻译已收集")
    if maillist[targetmail].stat == 1:
        maillist[targetmail].stat = 3
        scheduler.add_job(send2bili, trigger="date",
                          run_date=datetime.datetime.now() + datetime.timedelta(minutes=TIME_WAITBEFORESEND),
                          args=(maillist[targetmail], event), id=str(maillist[targetmail].no))
        await load_trans.finish(maillist[targetmail].preview())
    elif maillist[targetmail].stat == 3:
        scheduler.reschedule_job(str(maillist[targetmail].no), trigger="date",
                                 run_date=datetime.datetime.now() + datetime.timedelta(minutes=TIME_WAITBEFORESEND),
                                 )
        await load_trans.finish(maillist[targetmail].preview())
    else:
        maillist[targetmail].stat = 2


@get_blog.handle()
async def getblog(bot: Bot, event: GroupMessageEvent):
    try:
        blog = await get_latest_blog()
        content = convert_blog2message(blog)

        await get_blog.send(MessageSegment.text(content[0]))
        if content[1]:
            cnt = 0
            for img in content[1]:
                if img:
                    cnt += 1
                    await get_blog.send(f"第{cnt}张图片" + img)
        await get_blog.finish()

    except ValueError as errmsg:
        await get_blog.finish(f"获取最新博客失败：{errmsg}")


@scheduler.scheduled_job('cron', id='update_blog', hour="7-23", minute=f"*/{TIME_CHECKBLOGUPDATE}")
async def push_blog():
    blog = await check_if_blog_update()

    if blog:
        bot = nonebot.get_bot()

        await bot.send_group_msg(group_id=ADMINGROUPS[push_group], message=blog[0])
        if blog[1]:
            cnt = 0
            for img in blog[1]:
                if img:
                    cnt += 1
                    await bot.send_group_msg(group_id=ADMINGROUPS[push_group], message=f"第{cnt}张图片" + img)
        await bot.send_group_msg(group_id=ADMINGROUPS[push_group], message="我的博客更新啦ヾ(≧▽≦*)o，快来翻译")
    else:
        logger.info(f"没有检查到博客更新")


@get_twi.handle()
async def gettwi(bot: Bot, event: GroupMessageEvent):
    try:
        twi = await get_latest_twi()
        contents = await convert_twi2message(twi)

        for content in contents:
            await get_twi.send(content)
        await get_twi.finish()
    except ValueError as errmsg:
        await get_twi.finish(f"获取最新推文失败：{errmsg}")


@scheduler.scheduled_job('cron', id='update_twi', hour="7-23", minute=f"*/{TIME_CHECKTWIUPDATE}")
async def push_twi():
    twi = await check_if_twi_update()

    if twi:
        bot = nonebot.get_bot()

        for content in twi:
            notemsg = "\n—————————\n" \
                      "*如需发送动态请回复此消息并附上翻译内容"
            await bot.send_group_msg(group_id=ADMINGROUPS[push_group], message=content + notemsg)
    else:
        logger.info(f"没有检查到推特更新")


@scheduler.scheduled_job('cron', id='update_mail', hour="7-23", minute=f"*/{TIME_CHECKMAILUPDATE}")
async def push_mail():
    content, images, timstp = await check_if_mail_update()

    if content:
        for mail in maillist:
            if mail.time == int(timstp):
                logger.info("新mail已在列表中，跳过")
                return

        bot = nonebot.get_bot()

        mail = Mail()
        mail.raw_text = content
        mail.images = images
        mail.time = int(timstp)
        mail.stat = 1
        maillist.append(mail)
        logger.debug(f"当前mail时间戳：{mail.time}")

        await bot.send_group_msg(group_id=ADMINGROUPS[push_group], message=str(content).strip("\n"))
        if images:
            for image in images:
                await bot.send_group_msg(group_id=ADMINGROUPS[push_group], message=MessageSegment.image(image))
    else:
        logger.info(f"没有检查到Mail更新")


@send_by_reply.handle()
async def sendbyreply(bot: Bot, event: GroupMessageEvent, state: T_State):
    twi = Mail()
    raw_twi = event.reply.message
    if str(raw_twi[0]).find("推特更新") == -1:
        await send_by_reply.finish()

    twi.raw_text = str(raw_twi) if str(raw_twi) else "无"

    for content in raw_twi:
        if content.type == "image":
            async with AsyncClient() as client:
                img = await client.get(content.data["url"])
            twi.images.append(img.content)

    twi.translation = str(event.get_message()).strip(" ")

    maillist.append(twi)
    scheduler.add_job(send2bili, trigger="date",
                      run_date=datetime.datetime.now() + datetime.timedelta(minutes=TIME_WAITBEFORESEND),
                      args=(twi, event), id=str(twi.no))
    await send_by_reply.finish(twi.preview())
