import asyncio
import imghdr
import random
from io import BytesIO
from typing import Dict, List

import nonebot
import zhconv
from httpx import AsyncClient
from mysql.connector import Error as MySQLError
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.cqhttp.event import GroupMessageEvent
from nonebot.adapters.cqhttp.message import Message, MessageSegment
from nonebot.log import logger
from nonebot.typing import T_State
from nonebot.utils import run_sync

from .config import Config
from .data_source import SQLServer
from .model import Question, QuestionType, QuestionGroup

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
QNATIMEOUT = plugin_config.qna_timeout
SUPERGROUPS = plugin_config.qna_supergroups
SKIP_POSSIBILITY = plugin_config.qna_skip_possibility

groups_in_qna: List[int] = []
qnadict: Dict[int, Question] = {}  # {int(group_id): question}
lastqnaid: int = 0

sql = SQLServer()
scheduler = nonebot.require("nonebot_plugin_apscheduler").scheduler


async def checkifgroupinqna(bot: Bot, event: Event, state: T_State):
    if isinstance(event, GroupMessageEvent):
        if event.group_id in groups_in_qna:
            return True
    return False


qna_start = on_command("46问答", priority=10)
help_start = MessageSegment.text("☆启动问答：“46问答 {题号}”，不提供题号时将随机从题库中选择题目")
qna_answer = on_command("#", rule=checkifgroupinqna, priority=9)
help_answer = MessageSegment.text("☆回答问题：“# [回答]”，在回答前加#号")
qna_cancel = on_command("取消问答", priority=8)
help_cancel = MessageSegment.text("☆取消问答：“取消问答”，在答题过程中回复此命令取消答题过程")
qna_error = on_command("报错", priority=7)
help_error = MessageSegment.text("☆反馈错误：“报错”，仅在答题过程中可用，将会取消答题过程并向出题者们反馈有问题的题目。")
user_info = on_command("我的正确率", aliases={'我的信息'}, priority=7)
help_userinfo = MessageSegment.text("☆查询个人信息：“我的正确率”或“我的信息”，返回个人答题（正确率等）信息。")
user_skip = on_command("我想跳过", aliases={"我要跳过"}, priority=7)
help_skip = MessageSegment.text("☆设置不太想抽到的题目：“我想跳过 [类型1] [类型2]...”，"
                                f"在随机抽取题目时有较高的概率跳过指定类型，此设置仅对个人生效。")
test = on_command("测试", priority=1)


@test.handle()
async def _test(bot: Bot, event: GroupMessageEvent, state: T_State):
    ret = await bot.get_group_member_list(group_id=704330311, no_cache=True)  # 返回JSON数组
    logger.debug(repr(ret))
    await test.finish()


@qna_start.handle()
async def qnastart(bot: Bot, event: Event, state: T_State):
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        if group_id not in groups_in_qna:
            logger.info(f"收到来自群({group_id})的46问答请求")
            requester = event.get_user_id()
            skiptype = []
            question = None

            try:
                userdata = sql.get_user_info(requester)
                if userdata[3]:
                    for typ in userdata[3].split("|"):
                        skiptype.append(int(typ))
            except (IndexError, MySQLError):
                pass
            args = str(event.get_message()).strip()
            if args:
                try:
                    if args.isdecimal():
                        requid = int(args)
                        logger.info(f"指定题号模式，题号: {requid}")
                        question = sql.query(requid)
                    else:
                        raise IndexError
                except MySQLError as errmsg:
                    await qna_start.finish(str(errmsg))
                except IndexError:
                    logger.error("题目不存在或序号形式不合法")
                    await qna_start.finish("题目不存在或序号形式不合法")
                    return -1
            else:
                while True:
                    try:
                        randid = sql.get_latest_question_id()
                        if randid == -1 or random.randint(1, 100) >= 80:
                            randid = random.randint(1, sql.totl)
                        logger.info(f"随机题号模式，题号: {randid}")
                        question = sql.query(randid)
                        if question.if_err:
                            continue
                        if question.typ in skiptype and random.randint(1, 100) <= SKIP_POSSIBILITY:
                            continue
                        break
                    except MySQLError as errmsg:
                        await qna_start.finish(str(errmsg))
                    except IndexError:
                        continue

            logger.info(f"数据库返回题目信息: {question}")
            groups_in_qna.append(group_id)
            qnadict.update({group_id: question})
            scheduler.add_job(qnatimout, args=[group_id, event], trigger="interval", seconds=QNATIMEOUT,
                              id=f"{group_id}")

            msg = question.message_question()
            msg_no_record = Message([])
            msg_record = Message([])
            for msgseg in msg:
                if msgseg.type == "record":
                    msg_record.append(msgseg)
                else:
                    msg_no_record.append(msgseg)
            await qna_start.send(msg_no_record)
            if msg_record:
                for record in msg_record:
                    await qna_start.send(record)
            logger.info("题面消息发送完毕")

            await qna_start.finish()


async def qnatimout(group_id: int, event: GroupMessageEvent):
    logger.info(f"群({event.group_id})的问答已超时")
    question_temp = qnadict[event.group_id]
    await asyncio.gather(
        qnadataupdate(question_temp),
        qnaclear(group_id),
    )
    bot = nonebot.get_bot(str(event.self_id))
    await bot.send(event, message="时间到，没有人答出这个问题")


async def qnachecker(event: GroupMessageEvent) -> bool:
    # check if event from source group
    try:
        group_id = event.group_id
        if group_id in groups_in_qna:
            answer = str(event.get_message()).strip()
            logger.info(f"收到群({group_id}) 中 QQ({event.get_user_id()}) 的回答：{answer} ")
            qnadict[group_id].counttotal = qnadict[group_id].counttotal + 1

            answer = answer.lower().replace(" ", "").replace("\r\n", "")
            answer = zhconv.convert(answer, "zh-cn")
            solutions = qnadict[group_id].answers.split("|")
            logger.info(f"正确答案列表：{solutions}")
            for solution in solutions:
                solution = solution.lower().replace(" ", "")
                solution = zhconv.convert(solution, "zh-cn")
                if answer == solution:
                    logger.info(f"QQ({event.get_user_id()}) 回答正确！")
                    await qnauserupdate(qq=event.get_user_id(), state=True)
                    return True
            await qnauserupdate(qq=event.get_user_id(), state=False)
    except MySQLError as errmsg:
        await qna_answer.finish(str(errmsg))
    return False


@qna_answer.handle()
async def qnacomplete(bot: Bot, event: Event):
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        if group_id in qnadict:
            # try:
            #     await bot.call_api("delete_msg", message_id=int(event.message_id))
            # except nonebot.adapters.cqhttp.exception.ActionFailed:
            #     logger.error("群主和管理员的回答不能被撤回")
            checker_ret = await qnachecker(event)
            if not checker_ret:
                await qna_answer.finish()

            question_temp = qnadict[group_id]
            question_temp.countright = question_temp.countright + 1
            await asyncio.gather(
                bot.send(event, "回答正确！", at_sender=True),
                qnaclear(group_id),
                qnadataupdate(question_temp),
            )
            if question_temp.analysis is not None or question_temp.analfiles is not None:
                await bot.send(event, question_temp.message_analysis())


@qna_cancel.handle()
async def qnacancel(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    if group_id in groups_in_qna:
        # await matchers[group_id].finish()     # 注意到所有的matcher.finish()方法会通过raise exception提前结束函数
        question_temp = qnadict[group_id]
        await asyncio.gather(
            qnadataupdate(question_temp),
            qnaclear(group_id),
        )
        logger.info(f"群({group_id}) 中的问答被手动取消")
        await qna_cancel.finish("46问答已取消")
    else:
        await qna_cancel.finish("当前没有正在进行的46问答")


@qna_error.handle()
async def qnaerror(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    if group_id in groups_in_qna:
        question_temp = qnadict[group_id]
        question_temp.if_err = 1
        question_temp.counttotal += 1
        await asyncio.gather(
            qnadataupdate(question_temp),
            qnaclear(group_id),
        )
        logger.warning(f"群({event.group_id}) 中 QQ({event.get_user_id()}) "
                       f"对序号{question_temp.id}的问题报错，问答过程结束")
        await qna_error.finish("问题题目已标记，问答过程取消")
    else:
        await qna_cancel.finish("当前没有正在进行的46问答")


async def qnaclear(group_id: int):
    groups_in_qna.pop(groups_in_qna.index(group_id))
    qnadict.pop(group_id)
    scheduler.remove_job(f"{group_id}")
    logger.info(f"群({group_id}) 中的问答过程数据已清除")


@run_sync
def qnadataupdate(question: Question):
    try:
        sql.edit(question.id, dict(question))
        logger.info(f"更新题目序号{question.id}的问答记录")
    except MySQLError as errmsg:
        logger.error(errmsg)
    except IndexError:
        logger.error("更新题目问答记录时发现题目不存在")


@run_sync
def qnauserupdate(qq: int, state: bool):
    sql.update_user_counter(qq, state)


@user_info.handle()
async def userinfo(bot: Bot, event: Event):
    @run_sync
    def _getuserinfo(qq: str):
        return sql.get_user_info(qq)

    qq = event.get_user_id()
    try:
        ret = await _getuserinfo(qq)
    except MySQLError as errmsg:
        await user_info.finish(str(errmsg))
    except IndexError:
        await user_info.finish("没有查询到您的记录", at_sender=True)
    else:
        accuracy = round(ret[1] / ret[2] * 100, 2)
        await user_info.finish(f"\n〇答题次数：{ret[2]}\n"
                               f"●答题次数排名：{ret[4]}\n"
                               f"〇回答正确次数：{ret[1]}\n"
                               f"〇正确率：{accuracy}%\n"
                               f"●正确率排名：{ret[5]}\n\n"
                               f"〇偏向跳过题型：{ret[3]}", at_sender=True)


@user_skip.handle()
async def userskip(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = str(event.get_message()).strip()
    state["qq"] = event.get_user_id()
    if args:
        state["skiptype_raw"] = args


@user_skip.got("skiptype_raw", "请指定偏向*跳过*的题型，用空格分隔：\n"
                               "2:听歌猜名题\n"
                               "4:看图猜人题\n"
                               "0:清除所有设置")
async def userskip_main(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = state["skiptype_raw"].strip(" ").split(" ")
    state["skiptype"] = []
    for arg in args:
        if arg.isdecimal() and arg in ['2', '4', '0']:
            state["skiptype"].append(arg)
        else:
            await user_skip.finish("指定类型格式非数字或指定类型不在范围内", at_sender=True)
    if '0' in state["skiptype"]:
        state["skiptype"] = ""
    try:
        sql.update_user_skiptype(state["qq"], "|".join(state["skiptype"]))
        await user_skip.finish("偏好设置成功", at_sender=True)
    except MySQLError as errmsg:
        await user_skip.finish(str(errmsg))


async def ifsupergroup(bot: Bot, event: Event, state: T_State) -> bool:
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        if group_id in SUPERGROUPS:
            return True
    else:
        return False


question_info = on_command("46查询", aliases={'46#'}, rule=ifsupergroup, priority=5)
help_info = MessageSegment.text("★查询题目信息：“46查询 [题号]”或“46# [题号]”")
question_add = on_command("46出题", aliases={'46+'}, rule=ifsupergroup, priority=5)
help_add = MessageSegment.text("★添加新题目：“46出题 [题面]”或“46+ [题面]”")
question_addanal = on_command("46解析", aliases={'46++'}, rule=ifsupergroup, priority=7)
help_addanal = MessageSegment.text("★添加题目解析：“46解析 [题号] [解析]”或“46++ [题号] [解析]”")
question_editans = on_command("46答案", aliases={'46='}, rule=ifsupergroup, priority=6)
help_editans = MessageSegment.text("★修改题目答案：“46答案 [题号] [答案]”或“46= [题号] [答案]”")
question_edit = on_command("46修改", aliases={'46/'}, rule=ifsupergroup, priority=6)
help_edit = MessageSegment.text("★修改题目内容：“46修改 [题号] [题面]”或“46/ [题号] [题面]”")
question_del = on_command("46删除", aliases={'46-'}, rule=ifsupergroup, priority=8)
help_del = MessageSegment.text("★删除现有题目：“46删除 [题号]”或“46- [题号]”")

system_help = on_command('46帮助', aliases={'46?'}, priority=1)
help_help = MessageSegment.text("☆显示系统帮助：“46帮助”或“46?”")


async def extract_id(content: Message) -> int:
    if content[0].type != "text" or not content[0].data["text"].split(" ")[0].isdecimal():
        raise ValueError("错误的题号格式")
    quesid = int(content[0].data["text"].split(" ")[0])
    content[0].data["text"] = content[0].data["text"].strip(str(quesid)).strip(" ")
    return quesid


@question_info.handle()
async def questioninfo(bot: Bot, event: GroupMessageEvent):
    args = str(event.get_message()).strip()
    if args:
        try:
            if args.isdecimal():
                requid = int(args)
                question = sql.query(requid)
                await question_info.finish(question.message_all())
            else:
                raise IndexError
        except MySQLError as errmsg:
            await question_info.finish(str(errmsg))
        except IndexError:
            await question_info.finish("题目不存在或序号形式不合法")
    else:
        await question_info.finish("请使用“46# 题号”的形式查询题目信息")


async def _ques_parser(content: Message, state: T_State) -> str:
    state["files"] = []
    state["typ"] = QuestionType.text
    content_textonly = Message()
    for msgseg in content:
        if msgseg.type == "image":
            async with AsyncClient() as client:
                img = await client.get(msgseg.data["url"])
            img = BytesIO(img.content)
            filename = msgseg.data["file"].replace(".image", "") + "." + imghdr.what(img)
            with open(plugin_config.qna_imagecatagory.joinpath(filename), 'wb') as f:
                f.write(img.read())
            state["files"].append(filename)
            content_textonly += "\r\n"
        else:
            if msgseg.type == "text":
                text = msgseg.data["text"].strip(" ").strip("\r\n")
                content_textonly += text
            else:
                pass  # await question_add.send("检测到除文字和图片以外的内容，将被忽略")
    if not state["files"]:
        state["files"] = None
    else:
        state["typ"] = QuestionType.image
        state["files"] = "|".join(state["files"])
    content_textonly = str(content_textonly).strip("\r\n")
    if not state["files"] and not content_textonly:
        raise ValueError("题面内容为空")
    return content_textonly


@question_add.handle()
async def questionadd(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = event.get_message()
    if args:
        try:
            state["content"] = await _ques_parser(args, state)
            state["author"] = event.get_user_id()
            state["sakagroup"] = QuestionGroup.nogi
        except ValueError as errmsg:
            logger.error(f"出题者({state['author']}) 设置的题目内容不合规：{state['content']}")
            await question_add.finish(f"添加的题目内容不符合规范: {errmsg}")
    else:
        await question_add.finish('请使用“46+ 题面”的形式创建新题目')


async def _ans_parser(bot: Bot, event: Event, state: T_State, content=None):  # 手动代入content时不从event获取content
    if content is None:
        answer_raw = event.get_message()
        if str(answer_raw) == "取消":
            await question_add.finish("已取消，新题目将不会被收入题库")
        if len(answer_raw) != 1:
            await question_add.reject("答案中只能使用文字，请重新设置答案")
    else:
        answer_raw = content
    answer_parsed = None
    answer_raw = str(answer_raw)
    answer_raw = answer_raw.replace("\r\n", "")
    answer_raw = answer_raw.replace("\n", "")
    answer_raw = answer_raw.strip("|")
    answerlist_raw = answer_raw.split("|")
    answerlist = []
    for answer in answerlist_raw:
        if answer != "":
            answerlist.append(answer.strip(" "))
    if not answerlist:
        if content is None:
            raise ValueError("答案不合法，请重新设置答案")
        else:
            await question_add.reject("答案不合法，请重新设置答案")
    else:
        answer_parsed = "|".join(answerlist)
    state["answers"] = answer_parsed


@question_add.got("answers",
                  prompt="请回复答案，多个答案请用“|”符号隔开，不区分大小写或简繁体\n回复“取消”放弃创建" + MessageSegment.at("{author}"),
                  args_parser=_ans_parser)
async def questionadd_ans(bot: Bot, event: GroupMessageEvent, state: T_State):
    newquestion = Question(typ=state["typ"],
                           content=state["content"],
                           answers=state["answers"],
                           files=state["files"],
                           author=state["author"],
                           sakagroup=state["sakagroup"])
    try:
        quesid = sql.new(newquestion)
    except MySQLError:
        await question_add.finish("数据库出错")
    else:
        await question_add.finish(f"{quesid}题：题目创建成功")


async def _anal_parser(content: Message, state: T_State):
    state["analfiles"] = []
    anal_text = Message()
    for msgseg in content:
        if msgseg.type == "image":
            async with AsyncClient() as client:
                img = await client.get(msgseg.data["url"])
            img = BytesIO(img.content)
            filename = msgseg.data["file"].replace(".image", "") + "." + imghdr.what(img)
            with open(plugin_config.qna_imagecatagory.joinpath(filename), 'wb') as f:
                f.write(img.read())
            state["analfiles"].append(filename)
            anal_text += "\r\n"
        else:
            if msgseg.type == "text":
                text = msgseg.data["text"].strip(" ").strip("\r\n")
                anal_text += text
            else:
                await question_addanal.send("检测到除文字和图片以外的内容，将被忽略")
    if not state["analfiles"]:
        state["analfiles"] = None
    else:
        state["analfiles"] = "|".join(state["analfiles"])
    anal_text = str(anal_text).strip("\r\n")
    if not anal_text and not state["analfiles"]:
        raise ValueError("解析内容为空")
    state["analysis"] = anal_text


@question_addanal.handle()
async def questionadd_anal(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = event.get_message()
    try:
        if args:
            quesid = await extract_id(args)
            sql.query(quesid)
            await _anal_parser(args, state)
            analdict = {
                "analysis": state["analysis"],
                "analfiles": state["analfiles"],
            }
            sql.edit(quesid, analdict)
            logger.info(f"出题人({event.get_user_id()}) 添加/修改了题目序号{quesid}的解析")
            await question_addanal.finish(f"{quesid}题：解析修改成功")
        else:
            raise ValueError("解析内容为空")
    except MySQLError as errmsg:
        await question_addanal.finish(str(errmsg))
    except ValueError as errmsg:
        await question_addanal.finish(str(errmsg))
    except IndexError:
        await question_addanal.finish("题目不存在或序号形式不合法")


@question_editans.handle()
async def questioneditans(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = event.get_message()
    try:
        if args:
            quesid = await extract_id(args)
            sql.query(quesid)
            await _ans_parser(bot, event, state, content=args)
            answerdict = {
                "answers": state["answers"],
            }
            sql.edit(quesid, answerdict)
            logger.info(f"出题人({event.get_user_id()}) 修改了题目序号{quesid}的答案")
            await question_editans.finish(f"{quesid}题：答案修改成功")
        else:
            raise ValueError("没有指定题号")
    except MySQLError as errmsg:
        await question_editans.finish(str(errmsg))
    except ValueError as errmsg:
        await question_editans.finish(str(errmsg))
    except IndexError:
        await question_editans.finish("题目不存在或序号形式不合法")


@question_edit.handle()
async def questionedit(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = event.get_message()
    try:
        if args:
            quesid = await extract_id(args)
            sql.query(quesid)
            state["content"] = await _ques_parser(args, state)
            quesdict = {
                "typ": state["typ"],
                "files": state["files"],
                "content": state["content"],
            }
            sql.edit(quesid, quesdict)
            logger.info(f"出题人({event.get_user_id()}) 修改了题目序号{quesid}的题面")
            await question_edit.finish(f"{quesid}题：题面修改成功")
        else:
            raise ValueError("没有指定题号")
    except MySQLError as errmsg:
        await question_edit.finish(str(errmsg))
    except ValueError as errmsg:
        await question_edit.finish(str(errmsg))
    except IndexError:
        await question_edit.finish("题目不存在或序号形式不合法")


@question_del.handle()
async def questiondel(bot: Bot, event: GroupMessageEvent, state: T_State):
    args = event.get_message()
    try:
        if args:
            quesid = await extract_id(args)
            if not (sql.remove(quesid)):
                raise MySQLError("数据库错误")
            logger.warning(f"出题人({event.get_user_id()}) 删除了序号{quesid}的题目")
            await question_del.finish(f"{quesid}题：题目已经归档并移出题库")
        else:
            raise ValueError("没有指定题号")
    except MySQLError as errmsg:
        await question_del.finish(str(errmsg))
    except IndexError:
        await question_editans.finish("题目不存在或序号形式不合法")


@system_help.handle()
async def helpmenu(bot: Bot, event: GroupMessageEvent, state: T_State):
    msg = MessageSegment.text("【46问答系统帮助】\n")
    msg += MessageSegment.text("*请注意命令和参数之间都是有空格的*\n")
    if event.group_id in SUPERGROUPS:
        msg += help_info + "\n\n"
        msg += help_add + "\n\n"
        msg += help_addanal + "\n\n"
        msg += help_editans + "\n\n"
        msg += help_edit + "\n\n"
        msg += help_del + "\n"
    else:
        msg += help_start + "\n\n"
        msg += help_answer + "\n\n"
        msg += help_cancel + "\n\n"
        msg += help_error + "\n\n"
        msg += help_userinfo + "\n\n"
        msg += help_skip + "\n"
    await system_help.finish(msg)
